"""ACP-first provider wrapper with optional break-glass fallback."""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

from perlica.kernel.types import LLMRequest, LLMResponse
from perlica.providers.acp_client import ACPClient
from perlica.providers.acp_types import ACPClientConfig
from perlica.providers.base import (
    BaseProvider,
    ProviderContractError,
    ProviderInteractionHandler,
    ProviderProtocolError,
    ProviderTransportError,
)


ProviderEventEmitter = Callable[[str, Dict[str, Any], Dict[str, Any]], None]


class ACPProvider(BaseProvider):
    """Provider adapter that uses ACP as the primary execution path."""

    def __init__(
        self,
        *,
        provider_id: str,
        acp_config: ACPClientConfig,
        fallback_provider: Optional[BaseProvider] = None,
        event_emitter: Optional[ProviderEventEmitter] = None,
        interaction_handler: Optional[ProviderInteractionHandler] = None,
        interaction_resolver: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.provider_id = str(provider_id or "").strip().lower()
        self._acp_config = acp_config
        self._fallback_provider = fallback_provider
        self._event_emitter = event_emitter
        self._interaction_handler = interaction_handler
        self._interaction_resolver = interaction_resolver

    def generate(self, req: LLMRequest) -> LLMResponse:
        context = req.context if isinstance(req.context, dict) else {}
        client_kwargs: Dict[str, Any] = {
            "provider_id": self.provider_id,
            "config": self._acp_config,
            "event_sink": lambda event_type, payload: self._emit(event_type, payload, context),
            "interaction_handler": self._interaction_handler,
            "interaction_resolver": self._interaction_resolver,
        }
        try:
            client = ACPClient(**client_kwargs)
        except TypeError:
            # Backward compatibility for unit-test doubles that implement the
            # previous ACPClient constructor shape.
            client_kwargs.pop("interaction_handler", None)
            client_kwargs.pop("interaction_resolver", None)
            client = ACPClient(**client_kwargs)

        try:
            return client.generate(req)
        except ProviderContractError as exc:
            reason = str(exc)
            self._emit(
                "llm.invalid_response",
                {
                    "provider_id": self.provider_id,
                    "reason": "acp_contract_error",
                    "error": reason,
                },
                context,
            )
            raise ProviderContractError(
                reason,
                provider_id=self.provider_id,
                subtype="acp_contract_error",
            ) from exc
        except (ProviderTransportError, ProviderProtocolError) as exc:
            if self._break_glass_enabled() and self._fallback_provider is not None:
                self._emit(
                    "provider.fallback_activated",
                    {
                        "provider_id": self.provider_id,
                        "reason": str(exc),
                        "fallback_provider_id": self._fallback_provider.provider_id,
                    },
                    context,
                )
                return self._fallback_provider.generate(req)
            raise

    @staticmethod
    def _break_glass_enabled() -> bool:
        value = str(os.getenv("PERLICA_PROVIDER_BREAK_GLASS") or "").strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _emit(self, event_type: str, payload: Dict[str, Any], context: Dict[str, Any]) -> None:
        if self._event_emitter is None:
            return
        self._event_emitter(event_type, payload, context)
