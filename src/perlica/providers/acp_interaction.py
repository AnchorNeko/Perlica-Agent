"""ACP interaction request/answer mapping helpers."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from perlica.interaction.types import InteractionOption, InteractionRequest
from perlica.kernel.types import new_id


def parse_permission_request(notification: Dict[str, Any]) -> Optional[InteractionRequest]:
    """Parse ACP request-permission style notifications into InteractionRequest."""

    method = str(notification.get("method") or "").strip()
    if method not in {
        "session/request_permission",
        "session.request_permission",
        "session/requestPermission",
    }:
        return None

    params = notification.get("params")
    if not isinstance(params, dict):
        params = {}

    request_payload = params.get("request") if isinstance(params.get("request"), dict) else params

    interaction_id = _first_non_empty(
        request_payload.get("interaction_id"),
        request_payload.get("interactionId"),
        request_payload.get("request_id"),
        request_payload.get("requestId"),
        params.get("interaction_id"),
        params.get("interactionId"),
        params.get("request_id"),
        params.get("requestId"),
    )
    if not interaction_id:
        interaction_id = new_id("interaction")

    question = _first_non_empty(
        request_payload.get("question"),
        request_payload.get("prompt"),
        request_payload.get("message"),
        request_payload.get("text"),
        params.get("question"),
        params.get("prompt"),
        params.get("message"),
        params.get("text"),
    )
    if not question:
        question = "模型请求确认，请选择一个选项或输入自定义内容。"

    options_raw = request_payload.get("options")
    if not isinstance(options_raw, list):
        options_raw = request_payload.get("choices")
    if not isinstance(options_raw, list):
        options_raw = params.get("options") if isinstance(params.get("options"), list) else []

    options = _normalize_options(options_raw)
    allow_custom_input = _coerce_bool(
        _first_non_none(
            request_payload.get("allow_custom_input"),
            request_payload.get("allowCustomInput"),
            request_payload.get("allow_text_input"),
            request_payload.get("allowTextInput"),
            params.get("allow_custom_input"),
            params.get("allowCustomInput"),
        ),
        default=True,
    )

    return InteractionRequest(
        interaction_id=interaction_id,
        question=question,
        options=options,
        allow_custom_input=allow_custom_input,
        source_method=method,
        raw={
            "method": method,
            "params": dict(params),
        },
    )


def build_session_reply_params(
    *,
    session_id: str,
    session_key: str,
    interaction_id: str,
    selected_index: Optional[int],
    selected_option_id: str,
    custom_text: str,
    source: str,
) -> Dict[str, Any]:
    """Build tolerant ACP session/reply parameters."""

    params: Dict[str, Any] = {
        session_key: session_id,
        "interaction_id": interaction_id,
        "request_id": interaction_id,
        "source": source,
    }

    outcome: Dict[str, Any] = {}
    if selected_option_id:
        outcome["type"] = "option"
        outcome["option_id"] = selected_option_id
        outcome["selectedOptionId"] = selected_option_id
    if selected_index is not None:
        outcome["index"] = int(selected_index)
        outcome["selectedIndex"] = int(selected_index)
    if custom_text:
        outcome["type"] = "text"
        outcome["text"] = custom_text

    params["outcome"] = outcome
    params["reply"] = dict(outcome)
    if custom_text:
        params["custom_text"] = custom_text
    return params


def _normalize_options(items: Iterable[Any]) -> List[InteractionOption]:
    options: List[InteractionOption] = []
    next_index = 1
    for item in items:
        if not isinstance(item, dict):
            continue
        option_id = _first_non_empty(
            item.get("option_id"),
            item.get("optionId"),
            item.get("id"),
            item.get("value"),
            item.get("name"),
        )
        if not option_id:
            option_id = "option_{0}".format(next_index)

        label = _first_non_empty(
            item.get("label"),
            item.get("title"),
            item.get("text"),
            item.get("name"),
            option_id,
        )
        description = _first_non_empty(
            item.get("description"),
            item.get("detail"),
            item.get("hint"),
        )
        options.append(
            InteractionOption(
                index=next_index,
                option_id=option_id,
                label=label,
                description=description,
                meta=dict(item),
            )
        )
        next_index += 1
    return options


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default
