"""Built-in service channel adapters."""

from perlica.service.channels.imessage_adapter import IMessageChannelAdapter
from perlica.service.channels.registry import (
    ChannelRegistration,
    get_channel_registration,
    list_channel_registrations,
)

__all__ = [
    "IMessageChannelAdapter",
    "ChannelRegistration",
    "get_channel_registration",
    "list_channel_registrations",
]
