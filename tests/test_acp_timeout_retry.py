from __future__ import annotations

from typing import Any, Dict, List

from perlica.kernel.types import LLMRequest
from perlica.providers.acp_client import ACPClient
from perlica.providers.acp_transport import ACPTransportTimeout
from perlica.providers.acp_types import ACPClientConfig
from perlica.providers.base import ProviderProtocolError, ProviderTransportError


class _TimeoutOnceTransport:
    def __init__(self, config: ACPClientConfig, event_sink=None) -> None:
        self.config = config
        self.event_sink = event_sink
        self.restarts = 0
        self.prompt_calls = 0

    def request(self, payload: Dict[str, Any], timeout_sec: int) -> Dict[str, Any]:
        del timeout_sec
        method = str(payload.get("method") or "")
        request_id = str(payload.get("id") or "")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}
        if method == "session/new":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"session_id": "s1"}}
        if method == "session/prompt":
            self.prompt_calls += 1
            if self.prompt_calls == 1:
                raise ACPTransportTimeout("timeout")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "assistant_text": "retried",
                    "tool_calls": [],
                    "finish_reason": "stop",
                },
            }
        if method == "session/close":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"closed": True}}
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown"}}

    def restart(self) -> None:
        self.restarts += 1

    def close(self) -> None:
        return


def _request() -> LLMRequest:
    return LLMRequest(
        conversation_id="conv-timeout",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        context={},
    )


def test_acp_client_timeout_fails_without_retry(monkeypatch):
    transport = _TimeoutOnceTransport(config=ACPClientConfig(command="python3"))

    monkeypatch.setattr(
        "perlica.providers.acp_client.StdioACPTransport",
        lambda config, event_sink=None: transport,
    )

    events: List[tuple[str, Dict[str, Any]]] = []
    client = ACPClient(
        provider_id="claude",
        config=ACPClientConfig(command="python3", max_retries=2),
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    try:
        client.generate(_request())
        assert False, "expected ProviderTransportError"
    except ProviderTransportError as exc:
        assert "single-attempt failed due to timeout" in str(exc)

    assert transport.prompt_calls == 1
    assert transport.restarts == 0
    timeout_events = [name for name, _ in events if name == "acp.request.timeout"]
    assert timeout_events


def test_acp_client_does_not_retry_non_retryable_provider_error(monkeypatch):
    class _ProviderErrorTransport:
        def __init__(self, config: ACPClientConfig, event_sink=None) -> None:
            del config, event_sink
            self.prompt_calls = 0
            self.restarts = 0

        def request(self, payload: Dict[str, Any], timeout_sec: int) -> Dict[str, Any]:
            del timeout_sec
            method = str(payload.get("method") or "")
            request_id = str(payload.get("id") or "")
            if method == "initialize":
                return {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}
            if method == "session/new":
                return {"jsonrpc": "2.0", "id": request_id, "result": {"session_id": "s1"}}
            if method == "session/prompt":
                self.prompt_calls += 1
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32011, "message": "provider execution error", "data": {"error": "timeout"}},
                }
            if method == "session/close":
                return {"jsonrpc": "2.0", "id": request_id, "result": {"closed": True}}
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown"}}

        def restart(self) -> None:
            self.restarts += 1

        def close(self) -> None:
            return

    transport = _ProviderErrorTransport(config=ACPClientConfig(command="python3"))
    monkeypatch.setattr(
        "perlica.providers.acp_client.StdioACPTransport",
        lambda config, event_sink=None: transport,
    )

    client = ACPClient(
        provider_id="claude",
        config=ACPClientConfig(command="python3", max_retries=2),
        event_sink=lambda event_type, payload: None,
    )

    try:
        client.generate(_request())
        assert False, "expected ProviderProtocolError"
    except ProviderProtocolError as exc:
        assert "provider error" in str(exc)

    assert transport.prompt_calls == 1
    assert transport.restarts == 0
