from __future__ import annotations

from typing import Any, Dict, List

from perlica.kernel.types import LLMRequest
from perlica.providers.acp_client import ACPClient
from perlica.providers.acp_types import ACPClientConfig


class _FakeTransport:
    def __init__(self, config: ACPClientConfig, event_sink=None) -> None:
        self.config = config
        self.event_sink = event_sink
        self.requests: List[Dict[str, Any]] = []
        self.closed = False

    def request(self, payload: Dict[str, Any], timeout_sec: int) -> Dict[str, Any]:
        del timeout_sec
        self.requests.append(payload)
        method = str(payload.get("method") or "")
        request_id = str(payload.get("id") or "")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}
        if method == "session/new":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"session_id": "acp_sess_1"}}
        if method == "session/prompt":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "assistant_text": "ok",
                    "tool_calls": [],
                    "finish_reason": "stop",
                    "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1},
                },
            }
        if method == "session/close":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"closed": True}}
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown"}}

    def restart(self) -> None:
        return

    def close(self) -> None:
        self.closed = True


class _FakeTransportFactory:
    def __init__(self) -> None:
        self.instance: _FakeTransport | None = None

    def __call__(self, config: ACPClientConfig, event_sink=None) -> _FakeTransport:
        self.instance = _FakeTransport(config=config, event_sink=event_sink)
        return self.instance


def _request() -> LLMRequest:
    return LLMRequest(
        conversation_id="conv-1",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        context={
            "conversation_id": "conv-1",
            "run_id": "run-1",
            "trace_id": "trace-1",
        },
    )


def test_acp_client_runs_initialize_new_prompt_close(monkeypatch):
    factory = _FakeTransportFactory()
    monkeypatch.setattr("perlica.providers.acp_client.StdioACPTransport", factory)

    events: List[str] = []
    client = ACPClient(
        provider_id="claude",
        config=ACPClientConfig(command="python3"),
        event_sink=lambda event_type, payload: events.append(event_type),
    )

    response = client.generate(_request())

    assert response.assistant_text == "ok"
    assert response.tool_calls == []
    assert factory.instance is not None
    methods = [str(item.get("method") or "") for item in factory.instance.requests]
    assert methods == ["initialize", "session/new", "session/prompt", "session/close"]
    assert "acp.session.started" in events
    assert "acp.session.closed" in events
