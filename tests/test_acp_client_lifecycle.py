from __future__ import annotations

from typing import Any, Dict, List

from perlica.kernel.types import LLMRequest, LLMResponse
from perlica.providers.acp_client import ACPClient
from perlica.providers.acp_codec import ACPCodec
from perlica.providers.acp_codec_claude import ClaudeACPCodec
from perlica.providers.acp_codec_opencode import OpenCodeACPCodec
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
        codec=ClaudeACPCodec(),
        event_sink=lambda event_type, payload: events.append(event_type),
    )

    req = _request()
    req.context["provider_config"] = {"tool_execution_mode": "provider_managed"}
    response = client.generate(req)

    assert response.assistant_text == "ok"
    assert response.tool_calls == []
    assert factory.instance is not None
    methods = [str(item.get("method") or "") for item in factory.instance.requests]
    assert methods == ["initialize", "session/new", "session/prompt", "session/close"]
    session_new = factory.instance.requests[1].get("params") or {}
    assert isinstance(session_new, dict)
    assert "mcpServers" not in session_new
    assert "skills" not in session_new
    assert "provider.acp.session.started" in events
    assert "provider.acp.session.closed" in events


def test_claude_session_new_does_not_include_mcp_servers_even_when_declared(monkeypatch):
    factory = _FakeTransportFactory()
    monkeypatch.setattr("perlica.providers.acp_client.StdioACPTransport", factory)

    client = ACPClient(
        provider_id="claude",
        config=ACPClientConfig(command="python3"),
        codec=ClaudeACPCodec(),
    )

    req = _request()
    req.context["provider_config"] = {
        "mcp_servers": [],
    }
    response = client.generate(req)

    assert response.assistant_text == "ok"
    assert factory.instance is not None
    session_new = factory.instance.requests[1].get("params") or {}
    assert isinstance(session_new, dict)
    assert "mcpServers" not in session_new


def test_opencode_session_new_includes_empty_mcp_servers_even_without_runtime_injection(monkeypatch):
    factory = _FakeTransportFactory()
    monkeypatch.setattr("perlica.providers.acp_client.StdioACPTransport", factory)

    client = ACPClient(
        provider_id="opencode",
        config=ACPClientConfig(command="opencode", args=["acp"]),
        codec=OpenCodeACPCodec(),
    )

    req = _request()
    req.context["provider_config"] = {
        "tool_execution_mode": "provider_managed",
    }
    response = client.generate(req)

    assert response.assistant_text == "ok"
    assert factory.instance is not None
    session_new = factory.instance.requests[1].get("params") or {}
    assert isinstance(session_new, dict)
    assert "mcpServers" in session_new
    assert session_new.get("mcpServers") == []


class _FallbackTransport:
    def __init__(self, config: ACPClientConfig, event_sink=None) -> None:
        del config
        self.event_sink = event_sink
        self.requests: List[Dict[str, Any]] = []

    def request(
        self,
        payload: Dict[str, Any],
        timeout_sec: int,
        notification_sink=None,
        notification_handler=None,
        side_response_sink=None,
    ) -> Dict[str, Any]:
        del timeout_sec, notification_handler, side_response_sink
        self.requests.append(payload)
        method = str(payload.get("method") or "")
        request_id = str(payload.get("id") or "")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}
        if method == "session/new":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": "ses_1"}}
        if method == "session/prompt":
            if callable(notification_sink):
                notification_sink(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": "ses_1",
                            "update": {
                                "sessionUpdate": "available_commands_update",
                                "availableCommands": [],
                            },
                        },
                    }
                )
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "stopReason": "end_turn",
                    "message": "来自 opencode result 的兜底文本",
                    "usage": {"inputTokens": 10, "outputTokens": 5, "cachedReadTokens": 2},
                },
            }
        if method == "session/close":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found: session/close"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown"}}

    def restart(self) -> None:
        return

    def close(self) -> None:
        return


def test_acp_client_uses_visible_text_fallback_when_prompt_chunks_missing(monkeypatch):
    monkeypatch.setattr("perlica.providers.acp_client.StdioACPTransport", _FallbackTransport)
    events: List[str] = []
    client = ACPClient(
        provider_id="opencode",
        config=ACPClientConfig(command="opencode", args=["acp"]),
        codec=OpenCodeACPCodec(),
        event_sink=lambda event_type, payload: events.append(event_type),
    )
    response = client.generate(_request())
    assert response.assistant_text == "来自 opencode result 的兜底文本"
    assert response.finish_reason == "stop"
    assert response.usage.get("input_tokens") == 10
    assert response.usage.get("output_tokens") == 5
    assert "provider.acp.response.fallback_text_used" in events


class _ThoughtOnlyTransport:
    def __init__(self, config: ACPClientConfig, event_sink=None) -> None:
        del config
        self.event_sink = event_sink

    def request(
        self,
        payload: Dict[str, Any],
        timeout_sec: int,
        notification_sink=None,
        notification_handler=None,
        side_response_sink=None,
    ) -> Dict[str, Any]:
        del timeout_sec, notification_handler, side_response_sink
        method = str(payload.get("method") or "")
        request_id = str(payload.get("id") or "")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}
        if method == "session/new":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": "ses_2"}}
        if method == "session/prompt":
            if callable(notification_sink):
                notification_sink(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": "ses_2",
                            "update": {
                                "sessionUpdate": "agent_thought_chunk",
                                "content": {"type": "text", "text": "你好（来自 thought 兜底）"},
                            },
                        },
                    }
                )
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "stopReason": "end_turn",
                    "usage": {"inputTokens": 1, "outputTokens": 1},
                },
            }
        if method == "session/close":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found: session/close"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown"}}

    def restart(self) -> None:
        return

    def close(self) -> None:
        return


def test_acp_client_does_not_fallback_to_thought_only_chunks(monkeypatch):
    monkeypatch.setattr("perlica.providers.acp_client.StdioACPTransport", _ThoughtOnlyTransport)
    events: List[str] = []
    client = ACPClient(
        provider_id="opencode",
        config=ACPClientConfig(command="opencode", args=["acp"]),
        codec=OpenCodeACPCodec(),
        event_sink=lambda event_type, payload: events.append(event_type),
    )
    response = client.generate(_request())
    assert response.assistant_text == ""
    assert response.finish_reason == "stop"
    assert "provider.acp.response.fallback_text_used" not in events
    notifications = response.raw.get("notifications") if isinstance(response.raw, dict) else None
    assert isinstance(notifications, list)
    assert "agent_thought_chunk" in str(notifications[0])


class _StructuredVisibleFallbackTransport:
    def __init__(self, config: ACPClientConfig, event_sink=None) -> None:
        del config
        self.event_sink = event_sink

    def request(
        self,
        payload: Dict[str, Any],
        timeout_sec: int,
        notification_sink=None,
        notification_handler=None,
        side_response_sink=None,
    ) -> Dict[str, Any]:
        del timeout_sec, notification_sink, notification_handler, side_response_sink
        method = str(payload.get("method") or "")
        request_id = str(payload.get("id") or "")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}
        if method == "session/new":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": "ses_structured"}}
        if method == "session/prompt":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "stopReason": "end_turn",
                    "message": {
                        "type": "agent_message",
                        "content": [
                            {"type": "output_text", "text": "来自结构化 output_text 的回复"},
                        ],
                    },
                },
            }
        if method == "session/close":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found: session/close"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown"}}

    def restart(self) -> None:
        return

    def close(self) -> None:
        return


def test_acp_client_uses_structured_message_output_text_fallback(monkeypatch):
    monkeypatch.setattr("perlica.providers.acp_client.StdioACPTransport", _StructuredVisibleFallbackTransport)
    events: List[str] = []
    client = ACPClient(
        provider_id="opencode",
        config=ACPClientConfig(command="opencode", args=["acp"]),
        codec=OpenCodeACPCodec(),
        event_sink=lambda event_type, payload: events.append(event_type),
    )
    response = client.generate(_request())
    assert response.assistant_text == "来自结构化 output_text 的回复"
    assert response.finish_reason == "stop"
    assert "provider.acp.response.fallback_text_used" in events


class _StructuredThoughtFallbackTransport:
    def __init__(self, config: ACPClientConfig, event_sink=None) -> None:
        del config
        self.event_sink = event_sink

    def request(
        self,
        payload: Dict[str, Any],
        timeout_sec: int,
        notification_sink=None,
        notification_handler=None,
        side_response_sink=None,
    ) -> Dict[str, Any]:
        del timeout_sec, notification_sink, notification_handler, side_response_sink
        method = str(payload.get("method") or "")
        request_id = str(payload.get("id") or "")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}
        if method == "session/new":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": "ses_thought"}}
        if method == "session/prompt":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "stopReason": "end_turn",
                    "message": {
                        "type": "reasoning",
                        "text": "不应泄露的推理文本",
                    },
                },
            }
        if method == "session/close":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found: session/close"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown"}}

    def restart(self) -> None:
        return

    def close(self) -> None:
        return


def test_acp_client_does_not_fallback_to_structured_reasoning_message(monkeypatch):
    monkeypatch.setattr("perlica.providers.acp_client.StdioACPTransport", _StructuredThoughtFallbackTransport)
    events: List[str] = []
    client = ACPClient(
        provider_id="opencode",
        config=ACPClientConfig(command="opencode", args=["acp"]),
        codec=OpenCodeACPCodec(),
        event_sink=lambda event_type, payload: events.append(event_type),
    )
    response = client.generate(_request())
    assert response.assistant_text == ""
    assert response.finish_reason == "stop"
    assert "provider.acp.response.fallback_text_used" not in events


class _CodecBoundaryTransport:
    def __init__(self, config: ACPClientConfig, event_sink=None) -> None:
        del config, event_sink
        self.requests: List[Dict[str, Any]] = []

    def request(self, payload: Dict[str, Any], timeout_sec: int, **kwargs) -> Dict[str, Any]:
        del timeout_sec, kwargs
        self.requests.append(payload)
        method = str(payload.get("method") or "")
        request_id = str(payload.get("id") or "")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}
        if method == "session/new":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": "codec_sess"}}
        if method == "session/prompt":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}}
        if method == "session/close":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"closed": True}}
        raise AssertionError("unexpected method: {0}".format(method))

    def close(self) -> None:
        return


class _StubCodec(ACPCodec):
    def __init__(self) -> None:
        self.build_session_new_called = 0
        self.build_prompt_called = 0
        self.normalize_called = 0

    def build_session_new_params(self, *, req: LLMRequest, provider_id: str) -> Dict[str, Any]:
        del req
        self.build_session_new_called += 1
        return {"provider_id": provider_id, "custom_session_new": True}

    def extract_session_id(self, payload: Dict[str, Any]):
        del payload
        return "codec_sess", "sessionId"

    def build_prompt_params(
        self,
        *,
        req: LLMRequest,
        provider_id: str,
        session_id: str,
        session_key: str,
    ) -> Dict[str, Any]:
        del req
        self.build_prompt_called += 1
        return {
            "provider_id": provider_id,
            "session_id": session_id,
            "session_key": session_key,
            "custom_prompt": True,
        }

    def normalize_prompt_payload(
        self,
        *,
        payload: Dict[str, Any],
        notifications: List[Dict[str, Any]] | None,
        provider_id: str,
        event_sink=None,
    ) -> LLMResponse:
        del payload, notifications, event_sink
        self.normalize_called += 1
        return LLMResponse(
            assistant_text="codec-boundary-ok:{0}".format(provider_id),
            tool_calls=[],
            finish_reason="stop",
        )


def test_acp_client_codec_boundary_uses_codec_hooks(monkeypatch):
    transport = _CodecBoundaryTransport(config=ACPClientConfig(command="python3"))
    monkeypatch.setattr(
        "perlica.providers.acp_client.StdioACPTransport",
        lambda config, event_sink=None: transport,
    )
    codec = _StubCodec()
    client = ACPClient(
        provider_id="claude",
        config=ACPClientConfig(command="python3"),
        codec=codec,
    )

    response = client.generate(_request())
    assert response.assistant_text == "codec-boundary-ok:claude"
    assert codec.build_session_new_called == 1
    assert codec.build_prompt_called == 1
    assert codec.normalize_called == 1
    assert transport.requests[1].get("params", {}).get("custom_session_new") is True
    assert transport.requests[2].get("params", {}).get("custom_prompt") is True
