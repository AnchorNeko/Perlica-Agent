from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from perlica.interaction.types import InteractionAnswer, InteractionRequest
from perlica.kernel.types import LLMRequest
from perlica.providers.acp_client import ACPClient
from perlica.providers.acp_types import ACPClientConfig
from perlica.providers.base import ProviderProtocolError


class _InteractiveFakeTransport:
    def __init__(self, config: ACPClientConfig, event_sink=None) -> None:
        del config
        self.event_sink = event_sink
        self.requests: List[Dict[str, Any]] = []
        self.closed = False

    def request(
        self,
        payload: Dict[str, Any],
        timeout_sec: int,
        notification_sink=None,
        notification_handler=None,
        side_response_sink=None,
    ) -> Dict[str, Any]:
        del timeout_sec, notification_sink
        self.requests.append(payload)
        method = str(payload.get("method") or "")
        request_id = str(payload.get("id") or "")

        if method == "initialize":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}
        if method == "session/new":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"session_id": "acp_sess_1"}}
        if method == "session/close":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"closed": True}}

        if method == "session/prompt":
            if notification_handler is None:
                raise AssertionError("notification_handler must exist for interaction flow")

            side_request = notification_handler(
                {
                    "jsonrpc": "2.0",
                    "method": "session/request_permission",
                    "params": {
                        "interaction_id": "int_x",
                        "question": "你想添加什么内容的日程？",
                        "options": [
                            {"id": "meeting", "label": "会议"},
                            {"id": "todo", "label": "提醒事项"},
                        ],
                        "allow_custom_input": True,
                    },
                }
            )
            assert isinstance(side_request, dict)
            assert side_request.get("method") == "session/reply"
            self.requests.append(side_request)

            if side_response_sink is not None:
                side_response_sink(
                    {
                        "jsonrpc": "2.0",
                        "id": str(side_request.get("id") or ""),
                        "result": {"ok": True},
                    }
                )

            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "assistant_text": "收到，已继续执行。",
                    "tool_calls": [],
                    "finish_reason": "stop",
                    "usage": {"input_tokens": 5, "output_tokens": 7},
                },
            }

        raise AssertionError("unexpected method: {0}".format(method))

    def close(self) -> None:
        self.closed = True


class _Factory:
    def __init__(self) -> None:
        self.instance: Optional[_InteractiveFakeTransport] = None

    def __call__(self, config: ACPClientConfig, event_sink=None) -> _InteractiveFakeTransport:
        self.instance = _InteractiveFakeTransport(config=config, event_sink=event_sink)
        return self.instance


def _request() -> LLMRequest:
    return LLMRequest(
        conversation_id="conv-1",
        messages=[{"role": "user", "content": "帮我加个日程"}],
        tools=[],
        context={"run_id": "run_1", "trace_id": "trace_1"},
    )


def test_acp_client_handles_request_permission_and_replies(monkeypatch):
    factory = _Factory()
    monkeypatch.setattr("perlica.providers.acp_client.StdioACPTransport", factory)

    events: List[str] = []

    def _interaction_handler(request: InteractionRequest) -> InteractionAnswer:
        assert request.interaction_id == "int_x"
        assert request.question
        return InteractionAnswer(
            interaction_id=request.interaction_id,
            selected_index=1,
            selected_option_id="meeting",
            source="local",
            conversation_id=request.conversation_id,
            run_id=request.run_id,
            trace_id=request.trace_id,
            session_id=request.session_id,
        )

    client = ACPClient(
        provider_id="claude",
        config=ACPClientConfig(command="python3"),
        event_sink=lambda event_type, payload: events.append(event_type),
        interaction_handler=_interaction_handler,
    )

    response = client.generate(_request())

    assert response.assistant_text == "收到，已继续执行。"
    assert factory.instance is not None
    methods = [str(item.get("method") or "") for item in factory.instance.requests]
    assert methods == ["initialize", "session/new", "session/prompt", "session/reply", "session/close"]
    assert "acp.reply.sent" in events
    assert "interaction.resolved" in events


def test_acp_client_raises_when_interaction_handler_missing(monkeypatch):
    factory = _Factory()
    monkeypatch.setattr("perlica.providers.acp_client.StdioACPTransport", factory)

    client = ACPClient(
        provider_id="claude",
        config=ACPClientConfig(command="python3"),
        interaction_handler=None,
    )

    with pytest.raises(ProviderProtocolError):
        client.generate(_request())
