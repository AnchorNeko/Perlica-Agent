"""OpenCode ACP provider adapter."""

from __future__ import annotations

from typing import Callable, Optional

from perlica.providers.acp_codec import ACPCodec
from perlica.providers.acp_codec_opencode import OpenCodeACPCodec
from perlica.providers.acp_provider import ACPProviderBase, ProviderEventEmitter
from perlica.providers.acp_types import ACPClientConfig
from perlica.providers.base import ProviderInteractionHandler


class OpenCodeACPProvider(ACPProviderBase):
    """OpenCode provider implementation powered by ACP transport."""

    def __init__(
        self,
        *,
        provider_id: str,
        acp_config: ACPClientConfig,
        codec: Optional[ACPCodec] = None,
        event_emitter: Optional[ProviderEventEmitter] = None,
        interaction_handler: Optional[ProviderInteractionHandler] = None,
        interaction_resolver: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(
            provider_id=provider_id,
            acp_config=acp_config,
            codec=codec or OpenCodeACPCodec(),
            event_emitter=event_emitter,
            interaction_handler=interaction_handler,
            interaction_resolver=interaction_resolver,
        )
