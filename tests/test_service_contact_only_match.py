from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import perlica.service.orchestrator as orchestrator_module
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


class _MatchChannel:
    channel_name = "imessage"

    def __init__(self) -> None:
        self.listener: Optional[Callable[[ChannelInboundMessage], None]] = None
        self.sent: list[ChannelOutboundMessage] = []
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
        self.sent.append(outbound)

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


@dataclass
class _DummyRunnerResult:
    assistant_text: str
    session_id: str


class _DummyRunner:
    def __init__(self, runtime, provider_id, max_tool_calls, approval_resolver):
        del provider_id, max_tool_calls, approval_resolver
        self._runtime = runtime

    def run_text(self, text, assume_yes, session_ref):
        del text, assume_yes
        session = self._runtime.resolve_session_for_run(session_ref)
        return _DummyRunnerResult(
            assistant_text="业务回复",
            session_id=session.session_id,
        )


def test_service_binding_matches_contact_only_and_emits_mismatch(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _DummyRunner)

    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = _MatchChannel()
    events = []
    orchestrator = ServiceOrchestrator(
        runtime=runtime,
        store=store,
        channel=channel,
        provider_id=None,
        yes=True,
        event_sink=lambda event: events.append(event),
    )
    try:
        orchestrator.start()
        code = store.get_active_pairing_code("imessage")
        assert code is not None
        orchestrator._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/pair {0}".format(code),
                contact_id="bound@example.com",
                chat_id="chat-a",
                event_id="evt-pair-contact",
            )
        )

        sent_before = len(channel.sent)
        orchestrator._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="测试同联系人不同 chat",
                contact_id="bound@example.com",
                chat_id="chat-b",
                event_id="evt-same-contact",
            )
        )
        sent_texts = [item.text for item in channel.sent[sent_before:]]
        assert sent_texts[:2] == ["已收到🫡", "业务回复"]

        sent_before = len(channel.sent)
        orchestrator._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="这条不应触发回复",
                contact_id="other@example.com",
                chat_id="chat-a",
                event_id="evt-other-contact",
            )
        )
        assert len(channel.sent) == sent_before

        mismatch_events = [
            event for event in events if event.meta.get("reason") == "contact_mismatch"
        ]
        assert mismatch_events
        latest = mismatch_events[-1]
        assert latest.meta.get("bound_contact") == "bound@example.com"
        assert latest.meta.get("inbound_contact") == "other@example.com"
    finally:
        orchestrator.stop()
        store.close()
        runtime.close()
