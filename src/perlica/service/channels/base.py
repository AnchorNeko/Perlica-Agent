"""Channel adapter protocol for service bridge integrations."""

from __future__ import annotations

from typing import Callable, Optional, Protocol

from perlica.service.types import (
    ChannelBootstrapResult,
    ChannelHealthSnapshot,
    ChannelInboundMessage,
    ChannelOutboundMessage,
    ChannelTelemetryEvent,
)


class ChannelAdapter(Protocol):
    """Minimal channel contract to keep orchestrator channel-agnostic."""

    channel_name: str

    def probe(self) -> None:
        """Validate channel dependencies are available in current environment."""

    def bootstrap(self) -> ChannelBootstrapResult:
        """Perform channel bootstrap + permission checks before start."""

    def start_listener(self, callback: Callable[[ChannelInboundMessage], None]) -> None:
        """Start inbound message listener and push normalized events via callback."""

    def stop_listener(self) -> None:
        """Stop inbound listener and release resources."""

    def send_message(self, outbound: ChannelOutboundMessage) -> None:
        """Send one message to contact/chat."""

    def normalize_contact_id(self, raw: str) -> str:
        """Normalize contact id for stable matching and storage."""

    def set_telemetry_sink(
        self,
        sink: Optional[Callable[[ChannelTelemetryEvent], None]],
    ) -> None:
        """Set telemetry sink for raw adapter events (optional)."""

    def set_chat_scope(self, chat_id: Optional[str]) -> None:
        """Set channel-specific chat scope for inbound listening (optional for adapters)."""

    def poll_for_pairing_code(
        self,
        pairing_code: str,
        *,
        max_chats: int = 5,
    ) -> Optional[ChannelInboundMessage]:
        """Optionally poll channel history and return one `/pair <code>` match."""

    def health_snapshot(self) -> ChannelHealthSnapshot:
        """Return channel listener health and raw traffic counters."""
