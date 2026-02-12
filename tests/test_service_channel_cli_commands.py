from __future__ import annotations

from types import SimpleNamespace
from typing import Callable, Optional

import perlica.tui.service_controller as service_controller_module
from perlica.service.types import (
    ChannelBootstrapResult,
    ChannelHealthSnapshot,
    ChannelInboundMessage,
    ChannelOutboundMessage,
)
from perlica.tui.service_controller import ServiceController


class _FakeChannel:
    channel_name = "imessage"

    def __init__(self) -> None:
        self.listener: Optional[Callable[[ChannelInboundMessage], None]] = None
        self.health = ChannelHealthSnapshot(listener_state="running", listener_alive=True)

    def probe(self) -> None:
        return

    def bootstrap(self) -> ChannelBootstrapResult:
        return ChannelBootstrapResult(channel="imessage", ok=True, message="ok")

    def start_listener(self, callback: Callable[[ChannelInboundMessage], None]) -> None:
        self.listener = callback

    def stop_listener(self) -> None:
        self.listener = None

    def send_message(self, outbound: ChannelOutboundMessage) -> None:
        del outbound

    def normalize_contact_id(self, raw: str) -> str:
        return str(raw or "").strip().lower()

    def set_telemetry_sink(self, sink) -> None:
        del sink

    def set_chat_scope(self, chat_id: Optional[str]) -> None:
        del chat_id

    def poll_for_pairing_code(self, pairing_code: str, *, max_chats: int = 5):
        del pairing_code, max_chats
        return None

    def health_snapshot(self) -> ChannelHealthSnapshot:
        return ChannelHealthSnapshot(
            listener_state=self.health.listener_state,
            listener_alive=self.health.listener_alive,
            raw_inbound_count=self.health.raw_inbound_count,
            raw_outbound_count=self.health.raw_outbound_count,
            raw_line_count=self.health.raw_line_count,
            last_inbound_at_ms=self.health.last_inbound_at_ms,
            last_outbound_at_ms=self.health.last_outbound_at_ms,
            last_raw_line_preview=self.health.last_raw_line_preview,
            last_error=self.health.last_error,
        )


def test_service_channel_commands_and_inactive_text_hint(isolated_env, monkeypatch):
    registration = SimpleNamespace(
        channel_id="imessage",
        display_name="iMessage",
        description="desc",
        factory=_FakeChannel,
    )
    monkeypatch.setattr(
        service_controller_module,
        "list_channel_registrations",
        lambda: [registration],
    )
    monkeypatch.setattr(
        service_controller_module,
        "get_channel_registration",
        lambda _channel_id: registration,
    )
    monkeypatch.setattr(
        service_controller_module,
        "bootstrap_channel",
        lambda _channel: ChannelBootstrapResult(
            channel="imessage",
            ok=True,
            message="bootstrapped",
        ),
    )

    controller = ServiceController(provider="claude", yes=True, context_id="default")
    try:
        listed = controller.submit_input("/service channel list")
        assert "iMessage" in listed
        assert "imessage" in listed

        current_before = controller.submit_input("/service channel current")
        assert "没有激活渠道" in current_before

        warn_text = controller.submit_input("你好")
        assert "尚未激活渠道" in warn_text

        activated = controller.submit_input("/service channel use imessage")
        assert "渠道已激活" in activated
        assert controller.has_active_channel() is True

        current_after = controller.submit_input("/service channel current")
        assert "active_channel=imessage" in current_after
    finally:
        controller.close()


def test_service_tools_policy_commands(isolated_env, monkeypatch):
    registration = SimpleNamespace(
        channel_id="imessage",
        display_name="iMessage",
        description="desc",
        factory=_FakeChannel,
    )
    monkeypatch.setattr(
        service_controller_module,
        "list_channel_registrations",
        lambda: [registration],
    )
    monkeypatch.setattr(
        service_controller_module,
        "get_channel_registration",
        lambda _channel_id: registration,
    )
    monkeypatch.setattr(
        service_controller_module,
        "bootstrap_channel",
        lambda _channel: ChannelBootstrapResult(
            channel="imessage",
            ok=True,
            message="bootstrapped",
        ),
    )

    controller = ServiceController(provider="claude", yes=True, context_id="default")
    try:
        listed = controller.submit_input("/service tools list")
        assert "shell.exec" in listed
        assert "low=" in listed

        denied = controller.submit_input("/service tools deny shell.exec --risk low")
        assert "已禁止" in denied

        listed_after_deny = controller.submit_input("/service tools list")
        assert "shell.exec low=deny" in listed_after_deny

        allowed_all = controller.submit_input("/service tools allow --all --risk low")
        assert "已允许" in allowed_all
    finally:
        controller.close()
