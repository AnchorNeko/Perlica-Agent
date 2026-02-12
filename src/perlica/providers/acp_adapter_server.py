"""Built-in ACP stdio adapter server that bridges to legacy Claude CLI provider."""

from __future__ import annotations

import json
import queue
import sys
import threading
from typing import Any, Callable, Dict, Optional

from perlica.interaction.types import InteractionAnswer, InteractionRequest
from perlica.kernel.types import LLMRequest, ToolCall
from perlica.providers.base import ProviderContractError, ProviderError
from perlica.providers.claude_cli import ClaudeCLIProvider


class ACPServerError(RuntimeError):
    def __init__(self, code: int, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = message
        self.data = data or {}


class ACPAdapterServer:
    """Minimal ACP adapter server for legacy Claude CLI provider."""

    def __init__(
        self,
        *,
        notify: Optional[Callable[[Dict[str, Any]], None]] = None,
        prompt_heartbeat_sec: float = 1.0,
    ) -> None:
        self._active_provider_id: Optional[str] = None
        self._sessions: Dict[str, str] = {}
        self._notify = notify
        self._prompt_heartbeat_sec = max(0.1, float(prompt_heartbeat_sec))
        self._interaction_replies: Dict[str, "queue.Queue[InteractionAnswer]"] = {}
        self._interaction_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._providers = {
            "claude": ClaudeCLIProvider(event_sink=self._emit_provider_event),
        }

    def handle(self, request: Dict[str, Any]) -> Dict[str, Any]:
        request_id = request.get("id")
        method = str(request.get("method") or "")
        params = request.get("params")
        if not isinstance(params, dict):
            params = {}

        try:
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method == "session/new":
                result = self._handle_session_new(params)
            elif method == "session/prompt":
                result = self._handle_session_prompt(params)
            elif method == "session/reply":
                result = self._handle_session_reply(params)
            elif method == "session/close":
                result = self._handle_session_close(params)
            else:
                raise ACPServerError(-32601, "unknown method: {0}".format(method))
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        except ACPServerError as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "data": exc.data,
                },
            }
        except Exception as exc:  # pragma: no cover - defensive
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32000,
                    "message": "internal adapter error",
                    "data": {"error": str(exc)},
                },
            }

    def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        provider_id = str(params.get("provider_id") or "").strip().lower()
        if provider_id not in self._providers:
            raise ACPServerError(
                -32602,
                "unsupported provider_id: {0}".format(provider_id or "<empty>"),
            )
        self._active_provider_id = provider_id
        return {
            "provider_id": provider_id,
            "capabilities": {
                "sessions": True,
                "prompt": True,
            },
        }

    def _handle_session_new(self, params: Dict[str, Any]) -> Dict[str, Any]:
        provider_id = self._resolve_provider_id(params=params, require_initialized=True)
        session_id = str(params.get("session_id") or "").strip() or self._new_session_id()
        self._sessions[session_id] = provider_id
        return {"session_id": session_id}

    def _handle_session_prompt(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = str(params.get("session_id") or "").strip()
        if not session_id:
            raise ACPServerError(-32602, "session_id is required")

        provider_id = self._sessions.get(session_id)
        if not provider_id:
            provider_id = self._resolve_provider_id(params=params, require_initialized=True)
            self._sessions[session_id] = provider_id

        messages = params.get("messages")
        if not isinstance(messages, list):
            raise ACPServerError(-32602, "messages must be an array")

        tools = params.get("tools")
        if not isinstance(tools, list):
            raise ACPServerError(-32602, "tools must be an array")

        context = params.get("context") if isinstance(params.get("context"), dict) else {}
        conversation_id = str(params.get("conversation_id") or "").strip() or "acp"

        provider = self._providers.get(provider_id)
        if provider is None:
            raise ACPServerError(-32602, "unknown provider for session")

        request = LLMRequest(
            conversation_id=conversation_id,
            messages=[item for item in messages if isinstance(item, dict)],
            tools=[item for item in tools if isinstance(item, dict)],
            context=dict(context),
        )

        def _interaction_handler(interaction: InteractionRequest) -> InteractionAnswer:
            interaction_id = str(interaction.interaction_id or "").strip()
            if not interaction_id:
                raise ACPServerError(-32602, "interaction_id is required")
            reply_queue: "queue.Queue[InteractionAnswer]" = queue.Queue(maxsize=1)
            with self._interaction_lock:
                self._interaction_replies[interaction_id] = reply_queue
            self._emit_notification(
                "session/request_permission",
                {
                    "session_id": session_id,
                    "interaction_id": interaction_id,
                    "question": interaction.question,
                    "options": [
                        {
                            "option_id": option.option_id,
                            "label": option.label,
                            "description": option.description,
                            "index": option.index,
                        }
                        for option in interaction.options
                    ],
                    "allow_custom_input": bool(interaction.allow_custom_input),
                },
            )
            try:
                return reply_queue.get(timeout=600)
            except queue.Empty as exc:
                raise ACPServerError(-32013, "interaction reply timed out") from exc
            finally:
                with self._interaction_lock:
                    self._interaction_replies.pop(interaction_id, None)

        try:
            if isinstance(provider, ClaudeCLIProvider):
                response = provider.generate_with_interaction(
                    req=request,
                    interaction_handler=_interaction_handler,
                )
            else:
                response = provider.generate(request)
        except ProviderContractError as exc:
            raise ACPServerError(-32012, "provider contract error", {"error": str(exc)})
        except ProviderError as exc:
            raise ACPServerError(-32011, "provider execution error", {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive
            raise ACPServerError(-32000, "internal adapter error", {"error": str(exc)})

        return {
            "assistant_text": str(response.assistant_text or ""),
            "tool_calls": [
                {
                    "call_id": call.call_id,
                    "tool_name": call.tool_name,
                    "arguments": dict(call.arguments),
                    "risk_tier": call.risk_tier,
                }
                for call in response.tool_calls
                if isinstance(call, ToolCall)
            ],
            "finish_reason": str(response.finish_reason or "stop"),
            "usage": dict(response.usage or {}),
            "raw": dict(response.raw or {}),
        }

    def _handle_session_close(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = str(params.get("session_id") or "").strip()
        if session_id:
            self._sessions.pop(session_id, None)
        return {"closed": True, "session_id": session_id}

    def _handle_session_reply(self, params: Dict[str, Any]) -> Dict[str, Any]:
        interaction_id = str(params.get("interaction_id") or params.get("request_id") or "").strip()
        if not interaction_id:
            raise ACPServerError(-32602, "interaction_id is required for session/reply")

        outcome = params.get("outcome")
        if not isinstance(outcome, dict):
            outcome = params.get("reply") if isinstance(params.get("reply"), dict) else {}
        selected_index = outcome.get("index")
        if selected_index is None:
            selected_index = outcome.get("selectedIndex")
        try:
            normalized_index = int(selected_index) if selected_index is not None else None
        except (TypeError, ValueError):
            normalized_index = None
        answer = InteractionAnswer(
            interaction_id=interaction_id,
            selected_index=normalized_index,
            selected_option_id=str(
                outcome.get("option_id") or outcome.get("selectedOptionId") or ""
            ).strip(),
            custom_text=str(
                outcome.get("text") or params.get("custom_text") or ""
            ).strip(),
            source=str(params.get("source") or "local"),
        )

        with self._interaction_lock:
            reply_queue = self._interaction_replies.get(interaction_id)
        if reply_queue is None:
            raise ACPServerError(-32014, "interaction not pending", {"interaction_id": interaction_id})
        try:
            reply_queue.put_nowait(answer)
        except queue.Full:
            raise ACPServerError(-32015, "interaction already answered", {"interaction_id": interaction_id})
        return {"accepted": True, "interaction_id": interaction_id}

    def _resolve_provider_id(self, params: Dict[str, Any], require_initialized: bool) -> str:
        provider_id = str(params.get("provider_id") or "").strip().lower()
        if provider_id:
            if provider_id not in self._providers:
                raise ACPServerError(-32602, "unsupported provider_id: {0}".format(provider_id))
            return provider_id
        if self._active_provider_id:
            return self._active_provider_id
        if require_initialized:
            raise ACPServerError(-32001, "adapter is not initialized")
        return ""

    def _new_session_id(self) -> str:
        return "acp_sess_{0}".format(len(self._sessions) + 1)

    def _emit_notification(self, method: str, params: Dict[str, Any]) -> None:
        if self._notify is None:
            return
        self._notify(
            {
                "jsonrpc": "2.0",
                "method": str(method or "").strip(),
                "params": dict(params),
            }
        )

    def _emit_provider_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        self._emit_notification(
            "perlica/provider_event",
            {
                "event_type": str(event_type),
                "payload": dict(payload),
            },
        )


def _read_json_line(line: str) -> Optional[Dict[str, Any]]:
    text = line.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value


def _write_json_line(payload: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def main() -> int:
    server = ACPAdapterServer(notify=_write_json_line)
    write_lock = threading.Lock()

    def _write_response(response: Dict[str, Any]) -> None:
        with write_lock:
            _write_json_line(response)

    def _handle_async(request_payload: Dict[str, Any]) -> None:
        response_payload = server.handle(request_payload)
        _write_response(response_payload)

    for line in sys.stdin:
        request = _read_json_line(line)
        if request is None:
            continue
        method = str(request.get("method") or "")
        if method == "session/prompt":
            worker = threading.Thread(target=_handle_async, args=(dict(request),), daemon=True)
            worker.start()
            continue
        response = server.handle(request)
        _write_response(response)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
