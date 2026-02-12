"""Channel bootstrap coordinator with normalized error handling."""

from __future__ import annotations

from perlica.service.channels.base import ChannelAdapter
from perlica.service.types import ChannelBootstrapResult


def bootstrap_channel(channel: ChannelAdapter) -> ChannelBootstrapResult:
    """Run channel bootstrap and normalize failures into one result model."""

    try:
        bootstrap = getattr(channel, "bootstrap", None)
        if callable(bootstrap):
            result = bootstrap()
            if isinstance(result, ChannelBootstrapResult):
                return result

        channel.probe()
        return ChannelBootstrapResult(
            channel=channel.channel_name,
            ok=True,
            message="渠道初始化完成。",
        )
    except Exception as exc:
        return ChannelBootstrapResult(
            channel=getattr(channel, "channel_name", "unknown"),
            ok=False,
            message="渠道初始化失败：{0}".format(exc),
            needs_user_action=True,
        )

