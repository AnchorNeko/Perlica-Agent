"""Provider profile model and helpers for ACP-first provider selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


DEFAULT_PROVIDER_ID = "claude"
OPENCODE_PROVIDER_ID = "opencode"
ALLOWED_PROVIDER_IDS = (DEFAULT_PROVIDER_ID, OPENCODE_PROVIDER_ID)
DEFAULT_TOOL_EXECUTION_MODE = "provider_managed"
ALLOWED_TOOL_EXECUTION_MODES = ("provider_managed",)
DEFAULT_INJECTION_FAILURE_POLICY = "degrade"
ALLOWED_INJECTION_FAILURE_POLICIES = ("degrade", "fail")
DEFAULT_ADAPTER_COMMAND = "python3"
DEFAULT_ADAPTER_ARGS = ["-m", "perlica.providers.acp_adapter_server"]
OPENCODE_ADAPTER_COMMAND = "opencode"
OPENCODE_ADAPTER_ARGS = ["acp"]


@dataclass(frozen=True)
class ProviderProfile:
    """Runtime profile for one provider id."""

    provider_id: str
    enabled: bool = True
    adapter_command: str = DEFAULT_ADAPTER_COMMAND
    adapter_args: List[str] = field(default_factory=lambda: list(DEFAULT_ADAPTER_ARGS))
    adapter_env_allowlist: List[str] = field(default_factory=list)
    acp_connect_timeout_sec: int = 10
    acp_request_timeout_sec: int = 60
    acp_max_retries: int = 2
    acp_backoff: str = "exponential+jitter"
    acp_circuit_breaker_enabled: bool = True
    supports_mcp_config: bool = False
    supports_skill_config: bool = False
    tool_execution_mode: str = DEFAULT_TOOL_EXECUTION_MODE
    injection_failure_policy: str = DEFAULT_INJECTION_FAILURE_POLICY


def default_provider_profiles() -> Dict[str, ProviderProfile]:
    """Return default provider profile map for project bootstrap."""

    claude_profile = ProviderProfile(
        provider_id=DEFAULT_PROVIDER_ID,
        supports_mcp_config=True,
        supports_skill_config=True,
    )
    opencode_profile = ProviderProfile(
        provider_id=OPENCODE_PROVIDER_ID,
        adapter_command=OPENCODE_ADAPTER_COMMAND,
        adapter_args=list(OPENCODE_ADAPTER_ARGS),
        supports_mcp_config=True,
        supports_skill_config=True,
    )
    return {
        claude_profile.provider_id: claude_profile,
        opencode_profile.provider_id: opencode_profile,
    }
