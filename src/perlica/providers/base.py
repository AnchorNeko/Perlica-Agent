"""Provider interfaces and shared exceptions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Protocol

from perlica.kernel.types import LLMRequest, LLMResponse

if TYPE_CHECKING:
    from perlica.interaction.types import InteractionAnswer, InteractionRequest


class ProviderError(RuntimeError):
    """Raised when provider invocation fails."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self._details: Dict[str, Any] = {}
        for key, value in details.items():
            if value is None:
                continue
            self._details[str(key)] = value

    @property
    def details(self) -> Dict[str, Any]:
        return dict(self._details)


def provider_error_summary(exc: ProviderError) -> str:
    detail = exc.details if isinstance(exc, ProviderError) else {}
    ordered_keys = (
        "provider_id",
        "method",
        "code",
        "subtype",
        "request_id",
    )
    segments = [str(exc)]
    for key in ordered_keys:
        value = detail.get(key)
        if value in ("", None):
            continue
        segments.append("{0}={1}".format(key, value))
    raw_shape = detail.get("raw_shape")
    if isinstance(raw_shape, dict) and raw_shape:
        segments.append("raw_shape={0}".format(raw_shape))
    return " | ".join(segments)


class ProviderContractError(ProviderError):
    """Raised when provider output violates contract guarantees."""


class ProviderTransportError(ProviderError):
    """Raised when provider transport fails (connect/timeout/IO)."""


class ProviderProtocolError(ProviderError):
    """Raised when provider returns invalid ACP protocol envelopes."""


@dataclass
class ProviderDegradedResponse:
    """Controlled degraded response payload for contract failures."""

    assistant_text: str
    reason: str
    provider_id: str


class BaseProvider(ABC):
    provider_id: str

    @abstractmethod
    def generate(self, req: LLMRequest) -> LLMResponse:
        raise NotImplementedError


class ProviderInteractionHandler(Protocol):
    """Sync callback used by providers to resolve interactive confirmations."""

    def __call__(self, request: "InteractionRequest") -> "InteractionAnswer":
        ...
