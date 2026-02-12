from __future__ import annotations

import time
from typing import Callable, Optional

from perlica.config import load_settings
from perlica.kernel.runtime import Runtime
from perlica.service.orchestrator import ServiceOrchestrator
from perlica.service.store import ServiceStore
from perlica.service.types import (
    ChannelBootstrapResult,
    ChannelHealthSnapshot,
    ChannelInboundMessage,
    ChannelOutboundMessage,
)


class _SupervisorChannel:
    channel_name = "imessage"

    def __init__(self) -> None:
        self.listener: Optional[Callable[[ChannelInboundMessage], None]] = None
        self.health = ChannelHealthSnapshot(listener_state="running", listener_alive=True)
        self.start_calls = 0

    def probe(self) -> None:
        return

    def bootstrap(self) -> ChannelBootstrapResult:
        return ChannelBootstrapResult(channel="imessage", ok=True, message="ok")

    def start_listener(self, callback: Callable[[ChannelInboundMessage], None]) -> None:
        self.listener = callback
        self.start_calls += 1
        self.health.listener_state = "running"
        self.health.listener_alive = True

    def stop_listener(self) -> None:
        self.listener = None
        self.health.listener_state = "stopped"
        self.health.listener_alive = False

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
        return self.health


def test_service_supervisor_reconnects_after_listener_error(isolated_env):
    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = _SupervisorChannel()
    orchestrator = ServiceOrchestrator(
        runtime=runtime,
        store=store,
        channel=channel,
        provider_id=None,
        yes=True,
    )
    try:
        orchestrator.start()
        code = store.get_active_pairing_code("imessage")
        assert code is not None
        orchestrator._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/pair {0}".format(code),
                contact_id="reconn@example.com",
                chat_id="chat-reconn",
                event_id="evt-pair-reconn",
            )
        )

        initial = channel.start_calls
        channel.health.listener_state = "error"
        channel.health.listener_alive = False
        time.sleep(1.6)
        assert channel.start_calls == initial
    finally:
        orchestrator.stop()
        store.close()
        runtime.close()
