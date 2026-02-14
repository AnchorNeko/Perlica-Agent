from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import perlica.service.orchestrator as orchestrator_module
from perlica.config import load_settings
from perlica.interaction.types import InteractionOption, InteractionRequest
from perlica.kernel.runtime import Runtime
from perlica.service.orchestrator import ServiceOrchestrator
from perlica.service.store import ServiceStore
from perlica.service.types import (
    ChannelBootstrapResult,
    ChannelHealthSnapshot,
    ChannelInboundMessage,
    ChannelOutboundMessage,
)


class _Channel:
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

    def poll_recent_messages(self, **kwargs):
        del kwargs
        return []

    def health_snapshot(self) -> ChannelHealthSnapshot:
        return self.health


@dataclass
class _DummyRunnerResult:
    assistant_text: str
    session_id: str


class _FailRunner:
    def __init__(self, runtime, provider_id, max_tool_calls, approval_resolver):
        del runtime, provider_id, max_tool_calls, approval_resolver

    def run_text(self, text, assume_yes, session_ref):
        del text, assume_yes, session_ref
        raise AssertionError("runner should not be called for pending interaction answer")


def _publish_pending(orchestrator: ServiceOrchestrator, runtime: Runtime, interaction_id: str) -> None:
    runtime.interaction_coordinator.publish(
        InteractionRequest(
            interaction_id=interaction_id,
            question="你想添加什么内容？",
            options=[
                InteractionOption(index=1, option_id="meeting", label="会议"),
                InteractionOption(index=2, option_id="todo", label="提醒"),
            ],
            allow_custom_input=True,
            conversation_id="session.s.pending",
            run_id="run_pending",
            trace_id="trace_pending",
            session_id=str(orchestrator.state.session_ref or ""),
            provider_id="claude",
        )
    )


def _pair(orchestrator: ServiceOrchestrator, store: ServiceStore) -> None:
    code = store.get_active_pairing_code("imessage")
    assert code is not None
    orchestrator._process_inbound(
        ChannelInboundMessage(
            channel="imessage",
            text="/pair {0}".format(code),
            contact_id="remote@example.com",
            chat_id="chat-1",
            event_id="evt-pair",
        )
    )


def test_service_pending_choose_route_sends_ack_then_continue(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _FailRunner)
    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = _Channel()
    orchestrator = ServiceOrchestrator(runtime=runtime, store=store, channel=channel, provider_id=None, yes=True)
    try:
        orchestrator.start()
        _pair(orchestrator, store)
        _publish_pending(orchestrator, runtime, interaction_id="int_choose")

        before = len(channel.sent)
        orchestrator._on_channel_message(
            ChannelInboundMessage(
                channel="imessage",
                text="/choose 1",
                contact_id="remote@example.com",
                chat_id="chat-1",
                event_id="evt-choose",
                is_from_me=False,
            )
        )
        outbound = [item.text for item in channel.sent[before:]]
        assert outbound
        assert outbound[0] in {"已收到🫡", "已收到"}
        assert any("交互回答已提交" in item for item in outbound[1:])
        assert runtime.interaction_coordinator.has_pending() is False
    finally:
        orchestrator.stop()
        store.close()
        runtime.close()


def test_service_pending_plain_text_route_sends_ack_then_continue(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _FailRunner)
    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = _Channel()
    orchestrator = ServiceOrchestrator(runtime=runtime, store=store, channel=channel, provider_id=None, yes=True)
    try:
        orchestrator.start()
        _pair(orchestrator, store)
        _publish_pending(orchestrator, runtime, interaction_id="int_text")

        before = len(channel.sent)
        orchestrator._on_channel_message(
            ChannelInboundMessage(
                channel="imessage",
                text="我偏好 Python + 简洁风格",
                contact_id="remote@example.com",
                chat_id="chat-1",
                event_id="evt-text",
                is_from_me=False,
            )
        )
        outbound = [item.text for item in channel.sent[before:]]
        assert outbound
        assert outbound[0] in {"已收到🫡", "已收到"}
        assert any("交互回答已提交" in item for item in outbound[1:])
        assert runtime.interaction_coordinator.has_pending() is False
    finally:
        orchestrator.stop()
        store.close()
        runtime.close()

