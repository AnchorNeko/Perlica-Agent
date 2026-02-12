from __future__ import annotations

import os

import pytest

from perlica.kernel.types import LLMRequest
from perlica.providers.claude_cli import ClaudeCLIProvider


@pytest.mark.skipif(
    os.getenv("PERLICA_LIVE_PROVIDER") not in {"claude"},
    reason="Set PERLICA_LIVE_PROVIDER=claude to run live provider contract checks.",
)
def test_live_provider_minimal_contract():
    provider = ClaudeCLIProvider()

    req = LLMRequest(
        conversation_id="live",
        messages=[{"role": "user", "content": "Reply with assistant_text=ok and no tool calls"}],
        tools=[],
        context={},
    )
    response = provider.generate(req)
    assert isinstance(response.assistant_text, str)
    assert isinstance(response.tool_calls, list)
