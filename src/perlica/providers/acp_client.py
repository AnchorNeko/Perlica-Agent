"""ACP client lifecycle for provider.generate()."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from perlica.interaction.types import InteractionAnswer, InteractionRequest
from perlica.kernel.types import LLMRequest, LLMResponse, coerce_tool_calls, new_id
from perlica.providers.acp_interaction import (
    build_session_reply_params,
    parse_permission_request,
)
from perlica.providers.acp_transport import ACPTransportTimeout, StdioACPTransport
from perlica.providers.acp_types import ACPClientConfig, ACPRequestEnvelope
from perlica.providers.base import (
    ProviderContractError,
    ProviderInteractionHandler,
    ProviderProtocolError,
    ProviderTransportError,
)


ACPEventSink = Callable[[str, Dict[str, Any]], None]


class ACPClient:
    """Execute ACP initialize/new/prompt/close lifecycle."""

    def __init__(
        self,
        *,
        provider_id: str,
        config: ACPClientConfig,
        event_sink: Optional[ACPEventSink] = None,
        interaction_handler: Optional[ProviderInteractionHandler] = None,
        interaction_resolver: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._provider_id = str(provider_id or "").strip().lower()
        self._config = config
        self._event_sink = event_sink
        self._interaction_handler = interaction_handler
        self._interaction_resolver = interaction_resolver

    def generate(self, req: LLMRequest) -> LLMResponse:
        transport = StdioACPTransport(config=self._config, event_sink=self._emit)
        session_id = ""
        session_key = "session_id"
        session_started = False
        lifecycle_error: Optional[Exception] = None

        try:
            self._send_once(
                transport=transport,
                req=req,
                method="initialize",
                params={
                    "provider_id": self._provider_id,
                    "protocolVersion": 1,
                    "client": {
                        "name": "perlica",
                        "version": "2.0.0",
                    },
                },
            )

            session_payload = self._send_session_new_with_degrade(
                transport=transport,
                req=req,
            )
            session_id, session_key = self._extract_session_id(session_payload)
            if not session_id:
                raise ProviderProtocolError("acp session/new missing session_id")

            self._emit(
                "acp.session.started",
                {
                    "provider_id": self._provider_id,
                    "session_id": session_id,
                },
            )
            session_started = True

            prompt_notifications: List[Dict[str, Any]] = []
            prompt_payload = self._send_once(
                transport=transport,
                req=req,
                method="session/prompt",
                params=self._build_prompt_params(
                    req=req,
                    session_id=session_id,
                    session_key=session_key,
                ),
                notification_sink=prompt_notifications.append,
                session_id=session_id,
                session_key=session_key,
            )
            return self._normalize_prompt_payload(prompt_payload, notifications=prompt_notifications)
        except Exception as exc:
            lifecycle_error = exc
            raise
        finally:
            if session_started and session_id:
                try:
                    self._send_once(
                        transport=transport,
                        req=req,
                        method="session/close",
                        params={
                            "provider_id": self._provider_id,
                            session_key: session_id,
                        },
                    )
                    self._emit(
                        "acp.session.closed",
                        {
                            "provider_id": self._provider_id,
                            "session_id": session_id,
                        },
                    )
                except Exception as close_exc:
                    if self._is_optional_close_error(close_exc):
                        self._emit(
                            "acp.session.closed",
                            {
                                "provider_id": self._provider_id,
                                "session_id": session_id,
                                "close_unsupported": True,
                            },
                        )
                        close_exc = None
                    if close_exc is None:
                        pass
                    else:
                        self._emit(
                            "acp.session.failed",
                            {
                                "provider_id": self._provider_id,
                                "session_id": session_id,
                                "reason": "session_close_failed",
                                "error": str(close_exc),
                            },
                        )
                        if lifecycle_error is None:
                            raise
            elif lifecycle_error is not None:
                self._emit(
                    "acp.session.failed",
                    {
                        "provider_id": self._provider_id,
                        "session_id": session_id,
                        "reason": "session_lifecycle_failed",
                        "error": str(lifecycle_error),
                    },
                )

            transport.close()

    def _send_once(
        self,
        *,
        transport: StdioACPTransport,
        req: LLMRequest,
        method: str,
        params: Dict[str, Any],
        notification_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
        session_id: str = "",
        session_key: str = "session_id",
    ) -> Dict[str, Any]:
        attempt = 0
        request_id = new_id("acp_req")
        self._emit(
            "acp.request.sent",
            {
                "provider_id": self._provider_id,
                "method": method,
                "request_id": request_id,
                "attempt": attempt,
                "conversation_id": req.conversation_id,
            },
        )
        envelope = ACPRequestEnvelope(request_id=request_id, method=method, params=params)
        request_timeout_sec = int(self._config.request_timeout_sec)
        if method == "session/prompt":
            # For long reasoning workloads, wait until final response instead
            # of enforcing a local hard timeout in Perlica.
            request_timeout_sec = 0
        pending_reply_ids: Dict[str, str] = {}
        try:
            notification_handler = None
            side_response_sink = None
            if method == "session/prompt":
                notification_handler = self._build_prompt_notification_handler(
                    req=req,
                    session_id=session_id,
                    session_key=session_key,
                    pending_reply_ids=pending_reply_ids,
                )
                side_response_sink = self._build_side_response_sink(
                    method=method,
                    pending_reply_ids=pending_reply_ids,
                )
            response = self._request_transport(
                transport=transport,
                payload=envelope.as_json(),
                timeout_sec=request_timeout_sec,
                notification_sink=notification_sink,
                notification_handler=notification_handler,
                side_response_sink=side_response_sink,
            )
            return self._extract_response_result(method=method, request_id=request_id, response=response)
        except ACPTransportTimeout as exc:
            self._emit(
                "acp.request.timeout",
                {
                    "provider_id": self._provider_id,
                    "method": method,
                    "request_id": request_id,
                    "attempt": attempt,
                },
            )
            raise ProviderTransportError(
                "acp single-attempt failed due to timeout: method={0}".format(method),
                provider_id=self._provider_id,
                method=method,
                subtype="timeout",
                request_id=request_id,
            ) from exc
        except ProviderTransportError as exc:
            if exc.details:
                raise
            raise ProviderTransportError(
                "acp single-attempt failed: method={0} error={1}".format(method, exc),
                provider_id=self._provider_id,
                method=method,
                request_id=request_id,
            ) from exc
        except ProviderProtocolError as exc:
            self._emit(
                "acp.response.invalid",
                {
                    "provider_id": self._provider_id,
                    "method": method,
                    "request_id": request_id,
                    "attempt": attempt,
                    "error": str(exc),
                },
            )
            if exc.details:
                raise
            raise ProviderProtocolError(
                "acp single-attempt failed: method={0} error={1}".format(method, exc),
                provider_id=self._provider_id,
                method=method,
                request_id=request_id,
            ) from exc

    @staticmethod
    def _request_transport(
        *,
        transport: StdioACPTransport,
        payload: Dict[str, Any],
        timeout_sec: int,
        notification_sink: Optional[Callable[[Dict[str, Any]], None]],
        notification_handler: Optional[Callable[[Dict[str, Any]], Optional[Any]]] = None,
        side_response_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        if (
            notification_sink is None
            and notification_handler is None
            and side_response_sink is None
        ):
            return transport.request(payload, timeout_sec=timeout_sec)
        try:
            return transport.request(
                payload,
                timeout_sec=timeout_sec,
                notification_sink=notification_sink,
                notification_handler=notification_handler,
                side_response_sink=side_response_sink,
            )
        except TypeError:
            pass

        try:
            return transport.request(
                payload,
                timeout_sec=timeout_sec,
                notification_sink=notification_sink,
                notification_handler=notification_handler,
            )
        except TypeError:
            pass

        try:
            return transport.request(
                payload,
                timeout_sec=timeout_sec,
                notification_sink=notification_sink,
            )
        except TypeError:
            # Backward compatibility for unit-test doubles that do not
            # implement optional callback arguments.
            return transport.request(payload, timeout_sec=timeout_sec)

    def _build_prompt_notification_handler(
        self,
        *,
        req: LLMRequest,
        session_id: str,
        session_key: str,
        pending_reply_ids: Dict[str, str],
    ) -> Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]:
        def handler(notification: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            request = parse_permission_request(notification)
            if request is None:
                return None

            enriched_request = InteractionRequest(
                interaction_id=request.interaction_id,
                question=request.question,
                options=list(request.options),
                allow_custom_input=request.allow_custom_input,
                source_method=request.source_method,
                conversation_id=str(req.conversation_id or ""),
                run_id=str((req.context or {}).get("run_id") or ""),
                trace_id=str((req.context or {}).get("trace_id") or ""),
                session_id=session_id,
                provider_id=self._provider_id,
                raw=dict(request.raw or {}),
            )
            answer = self._resolve_interaction_answer(enriched_request)
            reply_payload = self._interaction_to_reply_payload(
                session_id=session_id,
                session_key=session_key,
                answer=answer,
            )
            reply_id = str(reply_payload.get("id") or "")
            pending_reply_ids[reply_id] = enriched_request.interaction_id
            return reply_payload

        return handler

    def _build_side_response_sink(
        self,
        *,
        method: str,
        pending_reply_ids: Dict[str, str],
    ) -> Callable[[Dict[str, Any]], None]:
        def sink(response: Dict[str, Any]) -> None:
            response_id = str(response.get("id") or "")
            interaction_id = pending_reply_ids.pop(response_id, "")
            error = response.get("error")
            if isinstance(error, dict):
                code = int(error.get("code") or 0)
                message = str(error.get("message") or "session/reply failed")
                self._emit(
                    "acp.reply.failed",
                    {
                        "provider_id": self._provider_id,
                        "interaction_id": interaction_id,
                        "request_id": response_id,
                        "code": code,
                        "error": message,
                        "method": method,
                    },
                )
                raise ProviderProtocolError(
                    "acp session/reply failed ({0}) {1}".format(code, message),
                    provider_id=self._provider_id,
                    method=method,
                    request_id=response_id,
                    code=code,
                    subtype="session_reply_failed",
                )

            self._emit(
                "interaction.resolved",
                {
                    "interaction_id": interaction_id,
                    "provider_id": self._provider_id,
                    "request_id": response_id,
                    "source": "provider_reply",
                },
            )
            if interaction_id and callable(self._interaction_resolver):
                try:
                    self._interaction_resolver(interaction_id)
                except Exception:
                    pass

        return sink

    def _resolve_interaction_answer(self, request: InteractionRequest) -> InteractionAnswer:
        if self._interaction_handler is None:
            self._emit(
                "acp.reply.failed",
                {
                    "provider_id": self._provider_id,
                    "interaction_id": request.interaction_id,
                    "reason": "missing_interaction_handler",
                },
            )
            raise ProviderProtocolError(
                "acp interaction request received without handler",
                provider_id=self._provider_id,
                method="session/prompt",
                subtype="missing_interaction_handler",
            )
        try:
            return self._interaction_handler(request)
        except ProviderProtocolError:
            raise
        except Exception as exc:
            self._emit(
                "acp.reply.failed",
                {
                    "provider_id": self._provider_id,
                    "interaction_id": request.interaction_id,
                    "reason": "interaction_handler_exception",
                    "error": str(exc),
                },
            )
            raise ProviderProtocolError(
                "acp interaction handler failed: {0}".format(exc),
                provider_id=self._provider_id,
                method="session/prompt",
                subtype="interaction_handler_exception",
            ) from exc

    def _interaction_to_reply_payload(
        self,
        *,
        session_id: str,
        session_key: str,
        answer: InteractionAnswer,
    ) -> Dict[str, Any]:
        params = build_session_reply_params(
            session_id=session_id,
            session_key=session_key,
            interaction_id=answer.interaction_id,
            selected_index=answer.selected_index,
            selected_option_id=answer.selected_option_id,
            custom_text=answer.custom_text,
            source=answer.source or "local",
        )
        reply_id = new_id("acp_reply")
        self._emit(
            "acp.reply.sent",
            {
                "provider_id": self._provider_id,
                "interaction_id": answer.interaction_id,
                "request_id": reply_id,
                "source": answer.source or "local",
            },
        )
        return {
            "jsonrpc": "2.0",
            "id": reply_id,
            "method": "session/reply",
            "params": params,
        }

    def _extract_response_result(
        self,
        *,
        method: str,
        request_id: str,
        response: Dict[str, Any],
    ) -> Dict[str, Any]:
        error = response.get("error")
        if isinstance(error, dict):
            code = int(error.get("code") or 0)
            message = str(error.get("message") or "acp server error")
            data = error.get("data")
            detail = ""
            if isinstance(data, dict) and data:
                detail = " data={0}".format(data)
            subtype = ""
            if isinstance(data, dict):
                subtype = str(data.get("subtype") or data.get("error") or "")
            if code in (-32011, -32012):
                # Provider execution/contract errors are not transient transport failures.
                raise ProviderProtocolError(
                    "acp server provider error ({0}) {1}{2}".format(code, message, detail),
                    provider_id=self._provider_id,
                    method=method,
                    code=code,
                    subtype=subtype,
                    request_id=request_id,
                    raw_shape=self._summarize_payload_shape(response),
                )
            raise ProviderTransportError(
                "acp server error ({0}) {1}{2}".format(code, message, detail),
                provider_id=self._provider_id,
                method=method,
                code=code,
                subtype=subtype,
                request_id=request_id,
                raw_shape=self._summarize_payload_shape(response),
            )

        result = response.get("result")
        if not isinstance(result, dict):
            self._emit(
                "acp.response.invalid",
                {
                    "provider_id": self._provider_id,
                    "method": method,
                    "request_id": request_id,
                    "reason": "missing_result",
                },
            )
            raise ProviderProtocolError(
                "acp response missing object result",
                provider_id=self._provider_id,
                method=method,
                request_id=request_id,
                raw_shape=self._summarize_payload_shape(response),
            )
        return result

    @staticmethod
    def _summarize_payload_shape(payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        keys = sorted(str(k) for k in payload.keys())
        return {
            "keys": keys[:24],
            "size": len(keys),
        }

    def _normalize_prompt_payload(
        self,
        payload: Dict[str, Any],
        notifications: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        # Legacy Perlica ACP adapter shape.
        if isinstance(payload.get("tool_calls"), list) and (
            "assistant_text" in payload or "finish_reason" in payload
        ):
            assistant_text = str(payload.get("assistant_text") or "")
            raw_calls = payload.get("tool_calls")
            tool_calls = coerce_tool_calls([item for item in raw_calls if isinstance(item, dict)])
            finish_reason = str(payload.get("finish_reason") or "stop")
            usage_payload = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            usage = self._normalize_usage_payload(usage_payload)
            return LLMResponse(
                assistant_text=assistant_text,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage,
                raw=dict(payload),
            )

        # Official ACP shape (for example: cc-acp): final request response
        # returns stopReason, while assistant content streams via notifications.
        stop_reason = str(payload.get("stopReason") or payload.get("stop_reason") or "").strip()
        if not stop_reason:
            raise ProviderContractError("acp result missing stopReason")

        notification_rows = list(notifications or [])
        assistant_text = self._collect_assistant_text(notification_rows)
        fallback_source = ""
        if not assistant_text:
            assistant_text, fallback_source = self._collect_visible_text_fallback(
                payload=payload,
                notifications=notification_rows,
            )
            if assistant_text:
                self._emit(
                    "acp.response.fallback_text_used",
                    {
                        "provider_id": self._provider_id,
                        "source": fallback_source,
                        "chars": len(assistant_text),
                    },
                )
        tool_calls = coerce_tool_calls(self._collect_tool_calls(notification_rows))
        usage_payload = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        usage = self._normalize_usage_payload(usage_payload)

        return LLMResponse(
            assistant_text=assistant_text,
            tool_calls=tool_calls,
            finish_reason=self._map_stop_reason(stop_reason),
            usage=usage,
            raw={
                "result": dict(payload),
                "notifications": notification_rows,
            },
        )

    def _send_session_new_with_degrade(
        self,
        *,
        transport: StdioACPTransport,
        req: LLMRequest,
    ) -> Dict[str, Any]:
        params = self._build_session_new_params(req)
        provider_config = self._resolve_provider_config(req)
        failure_policy = str(provider_config.get("injection_failure_policy") or "degrade").strip().lower()
        can_degrade = failure_policy == "degrade"

        if not can_degrade:
            return self._send_once(
                transport=transport,
                req=req,
                method="session/new",
                params=params,
            )

        pending_params = dict(params)
        degrade_steps = [field for field in ("skills", "mcpServers") if field in pending_params]
        if not degrade_steps:
            return self._send_once(
                transport=transport,
                req=req,
                method="session/new",
                params=pending_params,
            )
        for step, rejected_field in enumerate(degrade_steps, start=1):
            try:
                return self._send_once(
                    transport=transport,
                    req=req,
                    method="session/new",
                    params=pending_params,
                )
            except (ProviderTransportError, ProviderProtocolError) as exc:
                if not self._is_session_new_injection_rejection(exc):
                    raise
                if rejected_field not in pending_params:
                    raise
                self._emit(
                    "acp.session_new.injection_degraded",
                    {
                        "provider_id": self._provider_id,
                        "rejected_field": rejected_field,
                        "step": step,
                        "error": str(exc),
                        "error_type": exc.__class__.__name__,
                        "code": exc.details.get("code"),
                        "subtype": str(exc.details.get("subtype") or ""),
                    },
                )
                pending_params = self._drop_session_new_field(pending_params, rejected_field)

        return self._send_once(
            transport=transport,
            req=req,
            method="session/new",
            params=pending_params,
        )

    def _build_session_new_params(self, req: LLMRequest) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "provider_id": self._provider_id,
            "conversation_id": req.conversation_id,
            "cwd": self._resolve_cwd(req),
        }
        mcp_servers = self._resolve_mcp_servers(req)
        if self._should_include_mcp_servers(req):
            params["mcpServers"] = mcp_servers
        skills = self._resolve_skills(req)
        if skills:
            params["skills"] = skills
        return params

    @staticmethod
    def _drop_session_new_field(params: Dict[str, Any], field: str) -> Dict[str, Any]:
        reduced = dict(params)
        reduced.pop(field, None)
        return reduced

    @staticmethod
    def _is_session_new_injection_rejection(exc: Exception) -> bool:
        if not isinstance(exc, (ProviderTransportError, ProviderProtocolError)):
            return False
        details = exc.details if hasattr(exc, "details") else {}
        code = details.get("code")
        if isinstance(code, int) and code in {-32601, -32602}:
            return True
        text = str(exc or "").strip().lower()
        if not text:
            return False
        has_field_hint = any(token in text for token in ("skill", "mcp", "field", "param", "argument"))
        has_rejection_hint = any(
            token in text
            for token in ("unknown", "unsupported", "invalid", "unexpected", "not allowed", "not supported")
        )
        return has_field_hint and has_rejection_hint

    @staticmethod
    def _resolve_cwd(req: LLMRequest) -> str:
        context = req.context if isinstance(req.context, dict) else {}
        cwd = str(context.get("cwd") or "").strip()
        if cwd:
            return cwd
        return "."

    @staticmethod
    def _resolve_mcp_servers(req: LLMRequest) -> List[Dict[str, Any]]:
        provider_config = ACPClient._resolve_provider_config(req)
        mcp_servers = provider_config.get("mcp_servers")
        if isinstance(mcp_servers, list):
            return [
                ACPClient._normalize_mcp_server_entry(item, default_name="")
                for item in mcp_servers
                if isinstance(item, dict)
            ]
        if isinstance(mcp_servers, dict):
            items: List[Dict[str, Any]] = []
            for server_id, raw in mcp_servers.items():
                if not isinstance(raw, dict):
                    continue
                items.append(ACPClient._normalize_mcp_server_entry(raw, default_name=str(server_id)))
            return items

        # Backward compatibility: old path used context["mcp_servers"].
        context = req.context if isinstance(req.context, dict) else {}
        legacy = context.get("mcp_servers")
        if isinstance(legacy, list):
            return [
                ACPClient._normalize_mcp_server_entry(item, default_name="")
                for item in legacy
                if isinstance(item, dict)
            ]
        if not isinstance(legacy, dict):
            return []
        items: List[Dict[str, Any]] = []
        for server_id, raw in legacy.items():
            if not isinstance(raw, dict):
                continue
            items.append(ACPClient._normalize_mcp_server_entry(raw, default_name=str(server_id)))
        return items

    @staticmethod
    def _should_include_mcp_servers(req: LLMRequest) -> bool:
        provider_config = ACPClient._resolve_provider_config(req)
        if "mcp_servers" in provider_config:
            return True
        context = req.context if isinstance(req.context, dict) else {}
        return "mcp_servers" in context

    @staticmethod
    def _resolve_skills(req: LLMRequest) -> List[Dict[str, Any]]:
        provider_config = ACPClient._resolve_provider_config(req)
        raw_skills = provider_config.get("skills")
        if not isinstance(raw_skills, list):
            return []
        rows: List[Dict[str, Any]] = []
        seen_skill_ids: set[str] = set()
        for raw in raw_skills:
            if not isinstance(raw, dict):
                continue
            skill_id = str(raw.get("skill_id") or raw.get("id") or "").strip()
            if not skill_id or skill_id in seen_skill_ids:
                continue
            seen_skill_ids.add(skill_id)
            rows.append(
                {
                    "id": skill_id,
                    "name": str(raw.get("name") or "").strip(),
                    "description": str(raw.get("description") or "").strip(),
                    "priority": int(raw.get("priority") or 0),
                    "triggers": [
                        str(item).strip().lower()
                        for item in raw.get("triggers", [])
                        if str(item).strip()
                    ]
                    if isinstance(raw.get("triggers"), list)
                    else [],
                    "system_prompt": str(raw.get("system_prompt") or "").strip(),
                }
            )
        return rows

    @staticmethod
    def _resolve_provider_config(req: LLMRequest) -> Dict[str, Any]:
        context = req.context if isinstance(req.context, dict) else {}
        provider_config = context.get("provider_config")
        if isinstance(provider_config, dict):
            return dict(provider_config)
        return {}

    @staticmethod
    def _normalize_mcp_server_entry(raw: Dict[str, Any], default_name: str) -> Dict[str, Any]:
        command = str(raw.get("command") or "").strip()
        args = [str(item) for item in raw.get("args", []) if str(item).strip()] if isinstance(raw.get("args"), list) else []
        env_rows: List[Dict[str, str]] = []
        env_raw = raw.get("env")
        if isinstance(env_raw, dict):
            for key, value in env_raw.items():
                key_text = str(key).strip()
                if not key_text:
                    continue
                env_rows.append({"name": key_text, "value": str(value)})
        elif isinstance(env_raw, list):
            for item in env_raw:
                if not isinstance(item, dict):
                    continue
                key_text = str(item.get("name") or "").strip()
                if not key_text:
                    continue
                env_rows.append({"name": key_text, "value": str(item.get("value") or "")})

        name = str(
            raw.get("name") or raw.get("server_id") or default_name or command or "mcp-server"
        ).strip()
        return {
            "name": name,
            "command": command,
            "args": args,
            "env": env_rows,
        }

    @staticmethod
    def _extract_session_id(payload: Dict[str, Any]) -> Tuple[str, str]:
        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            return session_id, "session_id"
        session_id = str(payload.get("sessionId") or "").strip()
        if session_id:
            return session_id, "sessionId"
        return "", "session_id"

    def _build_prompt_params(
        self,
        *,
        req: LLMRequest,
        session_id: str,
        session_key: str,
    ) -> Dict[str, Any]:
        if session_key == "sessionId":
            return {
                "sessionId": session_id,
                "prompt": self._messages_to_prompt_blocks(req.messages),
            }
        return {
            "provider_id": self._provider_id,
            "session_id": session_id,
            "conversation_id": req.conversation_id,
            "messages": req.messages,
            "tools": req.tools,
            "context": req.context,
        }

    @staticmethod
    def _messages_to_prompt_blocks(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            text = ACPClient._message_text(item.get("content"))
            if not text:
                continue
            prefix = role if role else "message"
            blocks.append({"type": "text", "text": "[{0}] {1}".format(prefix, text)})
        if not blocks:
            blocks.append({"type": "text", "text": ""})
        return blocks

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str):
                return text
            return ""
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").strip().lower()
                if item_type == "text" and isinstance(item.get("text"), str):
                    parts.append(str(item.get("text")))
            return "\n".join([part for part in parts if part]).strip()
        return ""

    @staticmethod
    def _collect_assistant_text(notifications: List[Dict[str, Any]]) -> str:
        allowed_updates = {
            "agent_message_chunk",
            "agent_message",
            "assistant_message_chunk",
            "assistant_message",
            "message_chunk",
            "message",
        }
        parts: List[str] = []
        for row in notifications:
            if not isinstance(row, dict):
                continue
            params = row.get("params")
            if not isinstance(params, dict):
                continue
            update = params.get("update")
            if not isinstance(update, dict):
                continue
            update_type = str(update.get("sessionUpdate") or update.get("session_update") or "").strip()
            if update_type not in allowed_updates:
                continue
            content = update.get("content")
            text = ACPClient._extract_text_from_content_value(content)
            if text:
                parts.append(text)
                continue
            alt_text = str(update.get("text") or "").strip()
            if alt_text:
                parts.append(alt_text)
        return "".join(parts).strip()

    @staticmethod
    def _collect_visible_text_fallback(
        *,
        payload: Dict[str, Any],
        notifications: List[Dict[str, Any]],
    ) -> Tuple[str, str]:
        text = ACPClient._extract_text_from_result_payload(payload)
        if text:
            return text, "result_payload"
        text = ACPClient._collect_visible_text_from_notifications(notifications)
        if text:
            return text, "notification_fallback"
        return "", ""

    @staticmethod
    def _extract_text_from_result_payload(payload: Dict[str, Any]) -> str:
        for key in ("assistant_text", "message", "output_text", "text", "result"):
            value = payload.get(key)
            text = ACPClient._extract_text_from_content_value(value)
            if text:
                return text
        content = payload.get("content")
        text = ACPClient._extract_text_from_content_value(content)
        if text:
            return text
        return ""

    @staticmethod
    def _collect_visible_text_from_notifications(notifications: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for row in notifications:
            if not isinstance(row, dict):
                continue
            params = row.get("params")
            if not isinstance(params, dict):
                continue
            update = params.get("update")
            if isinstance(update, dict):
                update_type = str(update.get("sessionUpdate") or update.get("session_update") or "").strip().lower()
                if "thought" in update_type:
                    continue
                if "message" in update_type:
                    text = ACPClient._extract_text_from_content_value(update.get("content"))
                    if text:
                        parts.append(text)
                        continue
                    alt_text = str(update.get("text") or "").strip()
                    if alt_text:
                        parts.append(alt_text)
                    continue

            # Some providers emit user-visible progress text directly under params
            # without wrapping it into params.update.
            if ACPClient._dict_looks_thought_like(params):
                continue
            text = ACPClient._extract_text_from_content_value(params)
            if text:
                parts.append(text)
        return "".join(parts).strip()

    @staticmethod
    def _extract_text_from_content_value(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            if ACPClient._dict_looks_thought_like(value):
                return ""
            text = value.get("text")
            if isinstance(text, str):
                return text.strip()
            for key in (
                "assistant_text",
                "message",
                "output_text",
                "text",
                "content",
                "result",
                "output",
                "value",
            ):
                if "thought" in key or "reasoning" in key:
                    continue
                nested = value.get(key)
                nested_text = ACPClient._extract_text_from_content_value(nested)
                if nested_text:
                    return nested_text
            return ""
        if isinstance(value, list):
            parts: List[str] = []
            for item in value:
                text = ACPClient._extract_text_from_content_value(item)
                if text:
                    parts.append(text)
            return "".join(parts).strip()
        return ""

    @staticmethod
    def _dict_looks_thought_like(value: Dict[str, Any]) -> bool:
        value_type = str(value.get("type") or value.get("kind") or "").strip().lower()
        if value_type and ("thought" in value_type or "reasoning" in value_type):
            return True
        for key in value.keys():
            key_text = str(key).strip().lower()
            if "thought" in key_text or "reasoning" in key_text:
                return True
        return False

    @staticmethod
    def _collect_tool_calls(notifications: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        calls: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for row in notifications:
            if not isinstance(row, dict):
                continue
            params = row.get("params")
            if not isinstance(params, dict):
                continue
            update = params.get("update")
            if not isinstance(update, dict):
                continue
            update_type = str(update.get("sessionUpdate") or update.get("session_update") or "").strip()
            if update_type != "tool_call":
                continue
            call_id = str(update.get("toolCallId") or update.get("tool_call_id") or "").strip()
            if not call_id or call_id in seen_ids:
                continue
            seen_ids.add(call_id)
            title = str(update.get("title") or "acp.tool_call")
            raw_input = update.get("rawInput")
            arguments = raw_input if isinstance(raw_input, dict) else {}
            calls.append(
                {
                    "call_id": call_id,
                    "tool_name": title,
                    "arguments": arguments,
                }
            )
        return calls

    @staticmethod
    def _normalize_usage_payload(usage_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "input_tokens": int(usage_payload.get("input_tokens") or usage_payload.get("inputTokens") or 0),
            "cached_input_tokens": int(
                usage_payload.get("cached_input_tokens")
                or usage_payload.get("cache_read_input_tokens")
                or usage_payload.get("cachedReadTokens")
                or 0
            ),
            "output_tokens": int(usage_payload.get("output_tokens") or usage_payload.get("outputTokens") or 0),
            "context_window": int(usage_payload.get("context_window") or 0),
            "raw_usage": dict(usage_payload),
        }

    @staticmethod
    def _map_stop_reason(stop_reason: str) -> str:
        reason = str(stop_reason or "").strip().lower()
        if reason in {"end_turn", "stop"}:
            return "stop"
        if reason in {"max_tokens", "max_turn_requests", "refusal", "cancelled"}:
            return reason
        return "stop"

    @staticmethod
    def _is_optional_close_error(exc: Exception) -> bool:
        text = str(exc or "").strip().lower()
        if not text:
            return False
        if "session/close" in text and "not found" in text:
            return True
        if "unknown method" in text and "session/close" in text:
            return True
        return False

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self._event_sink is None:
            return
        self._event_sink(event_type, payload)
