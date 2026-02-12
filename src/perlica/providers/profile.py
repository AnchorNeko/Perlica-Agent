"""Provider profile model and helpers for ACP-first provider selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


DEFAULT_PROVIDER_ID = "claude"
ALLOWED_PROVIDER_IDS = (DEFAULT_PROVIDER_ID,)
DEFAULT_PROVIDER_BACKEND = "acp"
ALLOWED_PROVIDER_BACKENDS = ("acp", "legacy_cli")
DEFAULT_ADAPTER_COMMAND = "python3"
DEFAULT_ADAPTER_ARGS = ["-m", "perlica.providers.acp_adapter_server"]


@dataclass(frozen=True)
class ProviderProfile:
    """Runtime profile for one provider id."""

    provider_id: str
    enabled: bool = True
    backend: str = DEFAULT_PROVIDER_BACKEND
    adapter_command: str = DEFAULT_ADAPTER_COMMAND
    adapter_args: List[str] = field(default_factory=lambda: list(DEFAULT_ADAPTER_ARGS))
    adapter_env_allowlist: List[str] = field(default_factory=list)
    acp_connect_timeout_sec: int = 10
    acp_request_timeout_sec: int = 60
    acp_max_retries: int = 2
    acp_backoff: str = "exponential+jitter"
    acp_circuit_breaker_enabled: bool = True
    fallback_enabled: bool = False


def default_provider_profiles() -> Dict[str, ProviderProfile]:
    """Return default provider profile map for project bootstrap."""

    profile = ProviderProfile(provider_id=DEFAULT_PROVIDER_ID)
    return {profile.provider_id: profile}
