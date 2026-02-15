"""Claude ACP codec implementation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from perlica.kernel.types import LLMRequest, LLMResponse, coerce_tool_calls
from perlica.providers.acp_codec import ACPCodecEventSink, ACPCodecSupport
from perlica.providers.base import ProviderContractError


class ClaudeACPCodec:
    """Codec that maps Claude ACP payloads to and from canonical structures."""

    def build_session_new_params(self, *, req: LLMRequest, provider_id: str) -> Dict[str, Any]:
        return {
            "provider_id": provider_id,
            "conversation_id": req.conversation_id,
            "cwd": ACPCodecSupport.resolve_cwd(req),
        }

    def extract_session_id(self, payload: Dict[str, Any]) -> Tuple[str, str]:
        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            return session_id, "session_id"
        session_id = str(payload.get("sessionId") or "").strip()
        if session_id:
            return session_id, "sessionId"
        return "", "session_id"

    def build_prompt_params(
        self,
        *,
        req: LLMRequest,
        provider_id: str,
        session_id: str,
        session_key: str,
    ) -> Dict[str, Any]:
        if session_key == "sessionId":
            return {
                "sessionId": session_id,
                "prompt": ACPCodecSupport.messages_to_prompt_blocks(req.messages),
            }
        return {
            "provider_id": provider_id,
            "session_id": session_id,
            "conversation_id": req.conversation_id,
            "messages": req.messages,
            "tools": req.tools,
            "context": req.context,
        }

    def normalize_prompt_payload(
        self,
        *,
        payload: Dict[str, Any],
        notifications: Optional[List[Dict[str, Any]]],
        provider_id: str,
        event_sink: Optional[ACPCodecEventSink] = None,
    ) -> LLMResponse:
        del provider_id, event_sink
        if isinstance(payload.get("tool_calls"), list) and (
            "assistant_text" in payload or "finish_reason" in payload
        ):
            assistant_text = str(payload.get("assistant_text") or "")
            raw_calls = payload.get("tool_calls")
            tool_calls = coerce_tool_calls([item for item in raw_calls if isinstance(item, dict)])
            finish_reason = str(payload.get("finish_reason") or "stop")
            usage_payload = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            usage = ACPCodecSupport.normalize_usage_payload(usage_payload)
            return LLMResponse(
                assistant_text=assistant_text,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage,
                raw=dict(payload),
            )

        stop_reason = str(payload.get("stopReason") or payload.get("stop_reason") or "").strip()
        if not stop_reason:
            raise ProviderContractError("acp result missing stopReason")

        notification_rows = list(notifications or [])
        assistant_text = ACPCodecSupport.collect_assistant_text(notification_rows)
        tool_calls = coerce_tool_calls(ACPCodecSupport.collect_tool_calls(notification_rows))
        usage_payload = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        usage = ACPCodecSupport.normalize_usage_payload(usage_payload)

        return LLMResponse(
            assistant_text=assistant_text,
            tool_calls=tool_calls,
            finish_reason=ACPCodecSupport.map_stop_reason(stop_reason),
            usage=usage,
            raw={
                "result": dict(payload),
                "notifications": notification_rows,
            },
        )
