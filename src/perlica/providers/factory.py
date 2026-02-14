"""Provider factory using profile-based configuration."""

from __future__ import annotations

from typing import Callable, Dict, Optional

from perlica.providers.acp_provider import ACPProvider
from perlica.providers.acp_types import ACPClientConfig
from perlica.providers.base import BaseProvider, ProviderInteractionHandler
from perlica.providers.claude_cli import ClaudeCLIProvider
from perlica.providers.profile import ALLOWED_PROVIDER_IDS, DEFAULT_PROVIDER_ID, ProviderProfile

ProviderEventEmitter = Callable[[str, Dict[str, object], Dict[str, object]], None]


class ProviderFactory:
    """Build provider instances from a provider profile."""

    def __init__(
        self,
        *,
        event_emitter: ProviderEventEmitter | None = None,
        interaction_handler: ProviderInteractionHandler | None = None,
        interaction_resolver: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._event_emitter = event_emitter
        self._interaction_handler = interaction_handler
        self._interaction_resolver = interaction_resolver

    def build(self, profile: ProviderProfile) -> BaseProvider:
        provider_id = str(profile.provider_id or "").strip().lower()
        if provider_id not in ALLOWED_PROVIDER_IDS:
            raise ValueError("unsupported provider profile: {0}".format(provider_id or "<empty>"))

        if profile.backend == "legacy_cli":
            if provider_id != DEFAULT_PROVIDER_ID:
                raise ValueError(
                    "legacy_cli backend is not supported for provider '{0}'".format(provider_id)
                )
            return ClaudeCLIProvider(
                interaction_handler=self._interaction_handler,
                interaction_resolver=self._interaction_resolver,
            )

        acp_config = ACPClientConfig(
            command=profile.adapter_command,
            args=list(profile.adapter_args),
            env_allowlist=list(profile.adapter_env_allowlist),
            connect_timeout_sec=int(profile.acp_connect_timeout_sec),
            request_timeout_sec=int(profile.acp_request_timeout_sec),
            max_retries=int(profile.acp_max_retries),
            backoff=str(profile.acp_backoff or "exponential+jitter"),
            circuit_breaker_enabled=bool(profile.acp_circuit_breaker_enabled),
        )

        fallback_provider = None
        if profile.fallback_enabled:
            if provider_id != DEFAULT_PROVIDER_ID:
                raise ValueError(
                    "fallback is only supported for provider '{0}'".format(DEFAULT_PROVIDER_ID)
                )
            fallback_provider = ClaudeCLIProvider(
                interaction_handler=self._interaction_handler,
                interaction_resolver=self._interaction_resolver,
            )
        return ACPProvider(
            provider_id=provider_id,
            acp_config=acp_config,
            fallback_provider=fallback_provider,
            event_emitter=self._event_emitter,
            interaction_handler=self._interaction_handler,
            interaction_resolver=self._interaction_resolver,
        )
