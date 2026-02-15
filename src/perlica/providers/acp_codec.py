"""ACP provider codec interfaces and shared helpers."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from perlica.kernel.types import LLMRequest, LLMResponse


ACPCodecEventSink = Callable[[str, Dict[str, Any]], None]


class ACPCodec(Protocol):
    """Provider-side ACP dialect contract used by ACPClient."""

    def build_session_new_params(self, *, req: LLMRequest, provider_id: str) -> Dict[str, Any]:
        ...

    def extract_session_id(self, payload: Dict[str, Any]) -> Tuple[str, str]:
        ...

    def build_prompt_params(
        self,
        *,
        req: LLMRequest,
        provider_id: str,
        session_id: str,
        session_key: str,
    ) -> Dict[str, Any]:
        ...

    def normalize_prompt_payload(
        self,
        *,
        payload: Dict[str, Any],
        notifications: Optional[List[Dict[str, Any]]],
        provider_id: str,
        event_sink: Optional[ACPCodecEventSink] = None,
    ) -> LLMResponse:
        ...


class ACPCodecSupport:
    """Shared pure helpers for provider codec implementations."""

    @staticmethod
    def resolve_cwd(req: LLMRequest) -> str:
        context = req.context if isinstance(req.context, dict) else {}
        cwd = str(context.get("cwd") or "").strip()
        return cwd or "."

    @staticmethod
    def messages_to_prompt_blocks(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            text = ACPCodecSupport.message_text(item.get("content"))
            if not text:
                continue
            prefix = role if role else "message"
            blocks.append({"type": "text", "text": "[{0}] {1}".format(prefix, text)})
        if not blocks:
            blocks.append({"type": "text", "text": ""})
        return blocks

    @staticmethod
    def message_text(content: Any) -> str:
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
    def collect_assistant_text(notifications: List[Dict[str, Any]]) -> str:
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
            text = ACPCodecSupport.extract_text_from_content_value(content)
            if text:
                parts.append(text)
                continue
            alt_text = str(update.get("text") or "").strip()
            if alt_text:
                parts.append(alt_text)
        return "".join(parts).strip()

    @staticmethod
    def collect_visible_text_fallback(
        *,
        payload: Dict[str, Any],
        notifications: List[Dict[str, Any]],
    ) -> Tuple[str, str]:
        text = ACPCodecSupport.extract_text_from_result_payload(payload)
        if text:
            return text, "result_payload"
        text = ACPCodecSupport.collect_visible_text_from_notifications(notifications)
        if text:
            return text, "notification_fallback"
        return "", ""

    @staticmethod
    def extract_text_from_result_payload(payload: Dict[str, Any]) -> str:
        for key in ("assistant_text", "message", "output_text", "text", "result"):
            value = payload.get(key)
            text = ACPCodecSupport.extract_text_from_content_value(value)
            if text:
                return text
        content = payload.get("content")
        text = ACPCodecSupport.extract_text_from_content_value(content)
        if text:
            return text
        return ""

    @staticmethod
    def collect_visible_text_from_notifications(notifications: List[Dict[str, Any]]) -> str:
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
                    text = ACPCodecSupport.extract_text_from_content_value(update.get("content"))
                    if text:
                        parts.append(text)
                        continue
                    alt_text = str(update.get("text") or "").strip()
                    if alt_text:
                        parts.append(alt_text)
                    continue

            if ACPCodecSupport.dict_looks_thought_like(params):
                continue
            text = ACPCodecSupport.extract_text_from_content_value(params)
            if text:
                parts.append(text)
        return "".join(parts).strip()

    @staticmethod
    def extract_text_from_content_value(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            if ACPCodecSupport.dict_looks_thought_like(value):
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
                nested_text = ACPCodecSupport.extract_text_from_content_value(nested)
                if nested_text:
                    return nested_text
            return ""
        if isinstance(value, list):
            parts: List[str] = []
            for item in value:
                text = ACPCodecSupport.extract_text_from_content_value(item)
                if text:
                    parts.append(text)
            return "".join(parts).strip()
        return ""

    @staticmethod
    def dict_looks_thought_like(value: Dict[str, Any]) -> bool:
        value_type = str(value.get("type") or value.get("kind") or "").strip().lower()
        if value_type and ("thought" in value_type or "reasoning" in value_type):
            return True
        for key in value.keys():
            key_text = str(key).strip().lower()
            if "thought" in key_text or "reasoning" in key_text:
                return True
        return False

    @staticmethod
    def collect_tool_calls(notifications: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
    def normalize_usage_payload(usage_payload: Dict[str, Any]) -> Dict[str, Any]:
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
    def map_stop_reason(stop_reason: str) -> str:
        reason = str(stop_reason or "").strip().lower()
        if reason in {"end_turn", "stop"}:
            return "stop"
        if reason in {"max_tokens", "max_turn_requests", "refusal", "cancelled"}:
            return reason
        return "stop"
