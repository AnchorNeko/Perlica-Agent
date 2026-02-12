from __future__ import annotations

from typing import Any, Dict, List

import pytest

from perlica.kernel.types import LLMRequest, LLMResponse
from perlica.providers.acp_provider import ACPProvider
from perlica.providers.acp_types import ACPClientConfig
from perlica.providers.base import BaseProvider, ProviderTransportError


class _FallbackProvider(BaseProvider):
    provider_id = "claude"

    def __init__(self) -> None:
        self.called = 0

    def generate(self, req: LLMRequest) -> LLMResponse:
        self.called += 1
        return LLMResponse(assistant_text="fallback ok", tool_calls=[], finish_reason="stop")


def _request() -> LLMRequest:
    return LLMRequest(
        conversation_id="conv",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        context={
            "conversation_id": "conv",
            "run_id": "run-1",
            "trace_id": "trace-1",
        },
    )


def test_break_glass_disabled_raises(monkeypatch):
    def _raise(*args, **kwargs):
        raise ProviderTransportError("acp down")

    monkeypatch.setattr("perlica.providers.acp_client.ACPClient.generate", _raise)
    monkeypatch.delenv("PERLICA_PROVIDER_BREAK_GLASS", raising=False)

    provider = ACPProvider(
        provider_id="claude",
        acp_config=ACPClientConfig(command="python3"),
        fallback_provider=_FallbackProvider(),
    )

    with pytest.raises(ProviderTransportError):
        provider.generate(_request())


def test_break_glass_enabled_uses_fallback(monkeypatch):
    def _raise(*args, **kwargs):
        raise ProviderTransportError("acp down")

    monkeypatch.setattr("perlica.providers.acp_client.ACPClient.generate", _raise)
    monkeypatch.setenv("PERLICA_PROVIDER_BREAK_GLASS", "1")

    emitted: List[tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    fallback = _FallbackProvider()
    provider = ACPProvider(
        provider_id="claude",
        acp_config=ACPClientConfig(command="python3"),
        fallback_provider=fallback,
        event_emitter=lambda event_type, payload, context: emitted.append((event_type, payload, context)),
    )

    response = provider.generate(_request())

    assert response.assistant_text == "fallback ok"
    assert fallback.called == 1
    names = [name for name, _, _ in emitted]
    assert "provider.fallback_activated" in names
