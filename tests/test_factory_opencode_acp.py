from __future__ import annotations

from perlica.providers.claude_acp_provider import ClaudeACPProvider
from perlica.providers.factory import ProviderFactory
from perlica.providers.opencode_acp_provider import OpenCodeACPProvider
from perlica.providers.profile import ProviderProfile


def test_factory_builds_opencode_acp_provider():
    profile = ProviderProfile(
        provider_id="opencode",
        adapter_command="opencode",
        adapter_args=["acp"],
    )
    provider = ProviderFactory().build(profile)
    assert isinstance(provider, OpenCodeACPProvider)
    assert provider.provider_id == "opencode"


def test_factory_builds_claude_acp_provider():
    profile = ProviderProfile(
        provider_id="claude",
        adapter_command="python3",
        adapter_args=["-m", "perlica.providers.acp_adapter_server"],
    )
    provider = ProviderFactory().build(profile)
    assert isinstance(provider, ClaudeACPProvider)
    assert provider.provider_id == "claude"
