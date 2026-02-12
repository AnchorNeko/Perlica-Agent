"""Channel registry for service bridge adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

from perlica.service.channels.base import ChannelAdapter
from perlica.service.channels.imessage_adapter import IMessageChannelAdapter


@dataclass(frozen=True)
class ChannelRegistration:
    """Metadata + factory for one bridge channel."""

    channel_id: str
    display_name: str
    description: str
    factory: Callable[[], ChannelAdapter]


def _registrations() -> Dict[str, ChannelRegistration]:
    return {
        "imessage": ChannelRegistration(
            channel_id="imessage",
            display_name="iMessage",
            description="通过 imsg CLI 连接手机消息 (iMessage via imsg)",
            factory=IMessageChannelAdapter,
        )
    }


def list_channel_registrations() -> List[ChannelRegistration]:
    """List all built-in channel registrations."""

    return [entry for _, entry in sorted(_registrations().items())]


def get_channel_registration(channel_id: str) -> ChannelRegistration:
    """Resolve one registration by channel id."""

    normalized = str(channel_id or "").strip().lower()
    registrations = _registrations()
    if normalized not in registrations:
        raise ValueError("不支持的渠道：{0}".format(channel_id))
    return registrations[normalized]

