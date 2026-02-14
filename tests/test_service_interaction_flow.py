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


def test_service_remote_pending_answer_shortcuts_queue_and_sends_ack(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _FailRunner)

    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = _Channel()
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
                contact_id="remote@example.com",
                chat_id="chat-1",
                event_id="evt-pair",
            )
        )

        runtime.interaction_coordinator.publish(
            InteractionRequest(
                interaction_id="int_service_1",
                question="你想添加什么内容？",
                options=[
                    InteractionOption(index=1, option_id="meeting", label="会议"),
                    InteractionOption(index=2, option_id="todo", label="提醒"),
                ],
                allow_custom_input=True,
                conversation_id="session.s1",
                run_id="run_service",
                trace_id="trace_service",
                session_id=str(orchestrator.state.session_ref or ""),
                provider_id="claude",
            )
        )

        before = len(channel.sent)
        orchestrator._on_channel_message(
            ChannelInboundMessage(
                channel="imessage",
                text="2",
                contact_id="remote@example.com",
                chat_id="chat-1",
                event_id="evt-answer",
            )
        )

        outbound = [item.text for item in channel.sent[before:]]
        assert outbound
        assert outbound[0] in {"已收到🫡", "已收到"}
        assert any("交互回答已提交" in text for text in outbound)
        assert runtime.interaction_coordinator.has_pending() is False
    finally:
        orchestrator.stop()
        store.close()
        runtime.close()


def test_service_forwards_pending_interaction_to_bound_contact(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _FailRunner)

    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = _Channel()
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
                contact_id="remote@example.com",
                chat_id="chat-1",
                event_id="evt-pair-2",
            )
        )

        runtime.interaction_coordinator.publish(
            InteractionRequest(
                interaction_id="int_service_push_1",
                question="请选择一种风格",
                options=[
                    InteractionOption(index=1, option_id="compact", label="简洁"),
                    InteractionOption(index=2, option_id="verbose", label="详细"),
                ],
                allow_custom_input=True,
                conversation_id="session.s2",
                run_id="run_service_push",
                trace_id="trace_service_push",
                session_id=str(orchestrator.state.session_ref or ""),
                provider_id="claude",
            )
        )

        before = len(channel.sent)
        orchestrator._maybe_announce_pending_interaction()
        outbound = [item.text for item in channel.sent[before:]]
        assert outbound
        assert any("待确认交互" in text for text in outbound)
        assert any("1." in text and "2." in text for text in outbound)

        # Re-check should not duplicate for same interaction id.
        before_second = len(channel.sent)
        orchestrator._maybe_announce_pending_interaction()
        assert len(channel.sent) == before_second
    finally:
        orchestrator.stop()
        store.close()
        runtime.close()


def test_service_remote_choose_command_uses_fast_pending_path(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _FailRunner)

    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = _Channel()
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
                contact_id="remote@example.com",
                chat_id="chat-1",
                event_id="evt-pair-3",
            )
        )

        runtime.interaction_coordinator.publish(
            InteractionRequest(
                interaction_id="int_service_choose_1",
                question="请选择一种风格",
                options=[
                    InteractionOption(index=1, option_id="compact", label="简洁"),
                    InteractionOption(index=2, option_id="verbose", label="详细"),
                ],
                allow_custom_input=True,
                conversation_id="session.s3",
                run_id="run_service_choose",
                trace_id="trace_service_choose",
                session_id=str(orchestrator.state.session_ref or ""),
                provider_id="claude",
            )
        )

        before = len(channel.sent)
        orchestrator._on_channel_message(
            ChannelInboundMessage(
                channel="imessage",
                text="/choose 1",
                contact_id="remote@example.com",
                chat_id="chat-1",
                event_id="evt-choose",
            )
        )
        outbound = [item.text for item in channel.sent[before:]]
        assert any("已收到" in text for text in outbound)
        assert any("交互回答已提交" in text for text in outbound)
        assert runtime.interaction_coordinator.has_pending() is False
    finally:
        orchestrator.stop()
        store.close()
        runtime.close()


def test_service_from_me_choose_is_ignored_under_strict_inbound_policy(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _FailRunner)

    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = _Channel()
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
                contact_id="remote@example.com",
                chat_id="chat-1",
                event_id="evt-pair-4",
                is_from_me=False,
            )
        )

        runtime.interaction_coordinator.publish(
            InteractionRequest(
                interaction_id="int_service_choose_from_me_1",
                question="请选择一种风格",
                options=[
                    InteractionOption(index=1, option_id="compact", label="简洁"),
                    InteractionOption(index=2, option_id="verbose", label="详细"),
                ],
                allow_custom_input=True,
                conversation_id="session.s4",
                run_id="run_service_choose_from_me",
                trace_id="trace_service_choose_from_me",
                session_id=str(orchestrator.state.session_ref or ""),
                provider_id="claude",
            )
        )

        before = len(channel.sent)
        orchestrator._on_channel_message(
            ChannelInboundMessage(
                channel="imessage",
                text="/choose 1",
                contact_id="self@local.invalid",
                chat_id="chat-1",
                event_id="evt-answer-from-me",
                is_from_me=True,
            )
        )

        outbound = [item.text for item in channel.sent[before:]]
        assert outbound == []
        assert runtime.interaction_coordinator.has_pending() is True
    finally:
        orchestrator.stop()
        store.close()
        runtime.close()
