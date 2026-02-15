"""OpenCode ACP codec implementation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from perlica.kernel.types import LLMRequest, LLMResponse, coerce_tool_calls
from perlica.providers.acp_codec import ACPCodecEventSink, ACPCodecSupport
from perlica.providers.acp_codec_claude import ClaudeACPCodec
from perlica.providers.base import ProviderContractError


class OpenCodeACPCodec(ClaudeACPCodec):
    """OpenCode codec with user-visible fallback extraction safeguards."""

    def build_session_new_params(self, *, req: LLMRequest, provider_id: str) -> Dict[str, Any]:
        params = super().build_session_new_params(req=req, provider_id=provider_id)
        mcp_servers = params.get("mcpServers")
        if not isinstance(mcp_servers, list):
            params["mcpServers"] = []
        return params

    def normalize_prompt_payload(
        self,
        *,
        payload: Dict[str, Any],
        notifications: Optional[List[Dict[str, Any]]],
        provider_id: str,
        event_sink: Optional[ACPCodecEventSink] = None,
    ) -> LLMResponse:
        if isinstance(payload.get("tool_calls"), list) and (
            "assistant_text" in payload or "finish_reason" in payload
        ):
            return super().normalize_prompt_payload(
                payload=payload,
                notifications=notifications,
                provider_id=provider_id,
                event_sink=event_sink,
            )

        stop_reason = str(payload.get("stopReason") or payload.get("stop_reason") or "").strip()
        if not stop_reason:
            raise ProviderContractError("acp result missing stopReason")

        notification_rows = list(notifications or [])
        assistant_text = ACPCodecSupport.collect_assistant_text(notification_rows)
        fallback_source = ""
        if not assistant_text:
            assistant_text, fallback_source = ACPCodecSupport.collect_visible_text_fallback(
                payload=payload,
                notifications=notification_rows,
            )
            if assistant_text and callable(event_sink):
                event_sink(
                    "provider.acp.response.fallback_text_used",
                    {
                        "provider_id": provider_id,
                        "source": fallback_source,
                        "chars": len(assistant_text),
                    },
                )

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
