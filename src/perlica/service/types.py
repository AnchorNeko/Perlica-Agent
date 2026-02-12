"""Typed contracts shared by service bridge components."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from perlica.kernel.types import now_ms


@dataclass
class ChannelInboundMessage:
    """One normalized inbound message from any external channel."""

    channel: str
    text: str
    contact_id: str
    chat_id: Optional[str] = None
    event_id: str = ""
    is_from_me: bool = False
    ts_ms: int = field(default_factory=now_ms)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelOutboundMessage:
    """One outbound message request destined for a specific contact/chat."""

    channel: str
    text: str
    contact_id: str
    chat_id: Optional[str] = None


@dataclass
class PairingState:
    """Persistent binding state for one channel."""

    channel: str
    paired: bool
    contact_id: Optional[str]
    chat_id: Optional[str]
    session_id: Optional[str]
    paired_at_ms: Optional[int]
    updated_at_ms: int


@dataclass
class ChannelHealthSnapshot:
    """Channel runtime health snapshot, independent from any concrete platform."""

    listener_state: str = "unknown"
    listener_alive: bool = False
    raw_inbound_count: int = 0
    raw_outbound_count: int = 0
    raw_line_count: int = 0
    last_inbound_at_ms: Optional[int] = None
    last_outbound_at_ms: Optional[int] = None
    last_raw_line_preview: Optional[str] = None
    last_error: Optional[str] = None


@dataclass
class ServiceStatusSnapshot:
    """Service status exposed to TUI and command layer."""

    channel: str
    paired: bool
    contact_id: Optional[str]
    chat_id: Optional[str]
    session_id: Optional[str]
    pairing_code: Optional[str]
    received_bound_messages: int = 0
    ignored_messages: int = 0
    last_bound_inbound_at_ms: Optional[int] = None
    queue_depth: int = 0
    queue_max_depth: int = 0
    queue_busy: bool = False
    health: ChannelHealthSnapshot = field(default_factory=ChannelHealthSnapshot)


@dataclass
class ServiceEvent:
    """High-level service event for TUI/system logs."""

    kind: str
    text: str
    channel: str
    contact_id: Optional[str] = None
    chat_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelBootstrapResult:
    """Bootstrap / permission check result for one channel."""

    channel: str
    ok: bool
    message: str
    needs_user_action: bool = False
    opened_system_settings: bool = False


@dataclass
class ChannelTelemetryEvent:
    """Low-level channel telemetry emitted by adapters."""

    channel: str
    event_type: str
    direction: str = "internal"
    text: str = ""
    ts_ms: int = field(default_factory=now_ms)
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ServiceChannelOption:
    """One selectable channel option shown in service TUI."""

    channel_id: str
    display_name: str
    description: str
    available: bool
    reason: str = ""
