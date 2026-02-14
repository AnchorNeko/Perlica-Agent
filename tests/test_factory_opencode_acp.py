from __future__ import annotations

import pytest

from perlica.providers.acp_provider import ACPProvider
from perlica.providers.factory import ProviderFactory
from perlica.providers.profile import ProviderProfile


def test_factory_builds_opencode_acp_provider():
    profile = ProviderProfile(
        provider_id="opencode",
        backend="acp",
        adapter_command="opencode",
        adapter_args=["acp"],
    )
    provider = ProviderFactory().build(profile)
    assert isinstance(provider, ACPProvider)
    assert provider.provider_id == "opencode"


def test_factory_rejects_opencode_legacy_cli_backend():
    profile = ProviderProfile(
        provider_id="opencode",
        backend="legacy_cli",
    )
    with pytest.raises(ValueError) as exc_info:
        ProviderFactory().build(profile)
    assert "legacy_cli backend is not supported" in str(exc_info.value)


def test_factory_rejects_opencode_fallback_enabled():
    profile = ProviderProfile(
        provider_id="opencode",
        backend="acp",
        fallback_enabled=True,
    )
    with pytest.raises(ValueError) as exc_info:
        ProviderFactory().build(profile)
    assert "fallback is only supported" in str(exc_info.value)
