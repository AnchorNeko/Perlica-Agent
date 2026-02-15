"""ACP client lifecycle for provider.generate()."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from perlica.interaction.types import InteractionAnswer, InteractionRequest
from perlica.kernel.types import LLMRequest, LLMResponse, new_id
from perlica.providers.acp_codec import ACPCodec
from perlica.providers.acp_interaction import (
    build_session_reply_params,
    parse_permission_request,
)
from perlica.providers.acp_transport import ACPTransportTimeout, StdioACPTransport
from perlica.providers.acp_types import ACPClientConfig, ACPRequestEnvelope
from perlica.providers.base import (
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
        codec: ACPCodec,
        event_sink: Optional[ACPEventSink] = None,
        interaction_handler: Optional[ProviderInteractionHandler] = None,
        interaction_resolver: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._provider_id = str(provider_id or "").strip().lower()
        self._config = config
        self._codec = codec
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

            session_payload = self._send_once(
                transport=transport,
                req=req,
                method="session/new",
                params=self._codec.build_session_new_params(req=req, provider_id=self._provider_id),
            )
            session_id, session_key = self._codec.extract_session_id(session_payload)
            if not session_id:
                raise ProviderProtocolError("acp session/new missing session_id")

            self._emit(
                "provider.acp.session.started",
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
                params=self._codec.build_prompt_params(
                    req=req,
                    provider_id=self._provider_id,
                    session_id=session_id,
                    session_key=session_key,
                ),
                notification_sink=prompt_notifications.append,
                session_id=session_id,
                session_key=session_key,
            )
            return self._codec.normalize_prompt_payload(
                payload=prompt_payload,
                notifications=prompt_notifications,
                provider_id=self._provider_id,
                event_sink=self._emit,
            )
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
                        "provider.acp.session.closed",
                        {
                            "provider_id": self._provider_id,
                            "session_id": session_id,
                        },
                    )
                except Exception as close_exc:
                    if self._is_optional_close_error(close_exc):
                        self._emit(
                            "provider.acp.session.closed",
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
                            "provider.acp.session.failed",
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
                    "provider.acp.session.failed",
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
            "provider.acp.request.sent",
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
                "provider.acp.request.timeout",
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
                "provider.acp.response.invalid",
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
                    "provider.acp.reply.failed",
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
                "provider.acp.reply.failed",
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
                "provider.acp.reply.failed",
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
            "provider.acp.reply.sent",
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
                "provider.acp.response.invalid",
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
