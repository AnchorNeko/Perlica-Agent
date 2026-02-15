from __future__ import annotations

from typing import Any, Dict, List

import pytest

from perlica.kernel.types import LLMRequest
from perlica.providers.acp_types import ACPClientConfig
from perlica.providers.base import ProviderTransportError
from perlica.providers.claude_acp_provider import ClaudeACPProvider


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


def test_break_glass_removed_and_transport_error_propagates(monkeypatch):
    def _raise(*args, **kwargs):
        raise ProviderTransportError("acp down")

    monkeypatch.setattr("perlica.providers.acp_client.ACPClient.generate", _raise)
    monkeypatch.setenv("PERLICA_PROVIDER_BREAK_GLASS", "1")

    emitted: List[tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    provider = ClaudeACPProvider(
        provider_id="claude",
        acp_config=ACPClientConfig(command="python3"),
        event_emitter=lambda event_type, payload, context: emitted.append((event_type, payload, context)),
    )

    with pytest.raises(ProviderTransportError):
        provider.generate(_request())

    names = [name for name, _, _ in emitted]
    assert "provider.fallback_activated" not in names
