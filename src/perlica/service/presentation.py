"""Service-event to TUI-view presentation mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from perlica.service.types import ServiceEvent


@dataclass(frozen=True)
class ServiceEventView:
    """Render-ready event model for service TUI."""

    title: str
    border_style: str
    phase: str
    text: str


def map_service_event_to_view(event: ServiceEvent) -> Optional[ServiceEventView]:
    """Convert one service event to render metadata."""

    channel_label = str(event.channel or "").strip() or "channel"
    if event.kind == "inbound":
        return ServiceEventView(
            title="{0} 入站 (Inbound)".format(channel_label),
            border_style="#8a7bff",
            phase="收到远端消息 (Remote message)",
            text=event.text,
        )
    if event.kind == "ack":
        return ServiceEventView(
            title="{0} ACK".format(channel_label),
            border_style="#4db6ac",
            phase="已回执 (Acknowledged)",
            text=event.text,
        )
    if event.kind == "reply":
        return ServiceEventView(
            title="{0} 回复 (Reply)".format(channel_label),
            border_style="#4db6ac",
            phase="已回复 (Replied)",
            text=event.text,
        )
    if event.kind == "telemetry":
        event_type = str(event.meta.get("event_type") or "event")
        if event_type in {"inbound.polled", "inbound.ignored"}:
            # Hide high-frequency noise in TUI while keeping internal telemetry.
            return None

        direction = str(event.meta.get("direction") or "internal")
        return ServiceEventView(
            title="{0} telemetry/{1}".format(channel_label, direction),
            border_style="#6f7a8a",
            phase="监听中 (Listening)",
            text="{0}: {1}".format(event_type, event.text),
        )
    if event.kind == "error":
        return ServiceEventView(
            title="{0} 错误 (Error)".format(channel_label),
            border_style="#f25f5c",
            phase="监听异常 (Listener issue)",
            text=event.text,
        )
    return ServiceEventView(
        title="{0} 系统 (System)".format(channel_label),
        border_style="#d9b600",
        phase="监听中 (Listening)",
        text=event.text,
    )
