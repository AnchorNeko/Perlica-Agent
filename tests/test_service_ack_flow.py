from __future__ import annotations

from dataclasses import dataclass
import time
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


class _AckChannel:
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
        self.health.listener_alive = True
        self.health.listener_state = "running"

    def stop_listener(self) -> None:
        self.listener = None
        self.health.listener_alive = False
        self.health.listener_state = "stopped"

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


class _AckEmojiFailChannel(_AckChannel):
    def __init__(self) -> None:
        super().__init__()
        self._failed_once = False

    def send_message(self, outbound: ChannelOutboundMessage) -> None:
        if outbound.text == "已收到🫡" and not self._failed_once:
            self._failed_once = True
            raise RuntimeError("emoji not supported")
        super().send_message(outbound)


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


class _SlowRunner:
    def __init__(self, runtime, provider_id, max_tool_calls, approval_resolver):
        del provider_id, max_tool_calls, approval_resolver
        self._runtime = runtime

    def run_text(self, text, assume_yes, session_ref):
        del assume_yes
        session = self._runtime.resolve_session_for_run(session_ref)
        time.sleep(0.35)
        return _DummyRunnerResult(
            assistant_text="业务回复:{0}".format(text),
            session_id=session.session_id,
        )


def test_service_ack_then_final_reply_order(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _DummyRunner)

    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = _AckChannel()
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
                contact_id="ack@example.com",
                chat_id="chat-ack",
                event_id="evt-pair-ack",
            )
        )
        before = len(channel.sent)
        orchestrator._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="你好",
                contact_id="ack@example.com",
                chat_id="chat-ack",
                event_id="evt-ack-msg",
            )
        )
        texts = [item.text for item in channel.sent[before:]]
        assert texts[:2] == ["已收到🫡", "业务回复"]
    finally:
        orchestrator.stop()
        store.close()
        runtime.close()


def test_service_ack_fallback_still_allows_final_reply(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _DummyRunner)

    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = _AckEmojiFailChannel()
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
                contact_id="ack2@example.com",
                chat_id="chat-ack2",
                event_id="evt-pair-ack2",
            )
        )
        before = len(channel.sent)
        orchestrator._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="你好",
                contact_id="ack2@example.com",
                chat_id="chat-ack2",
                event_id="evt-ack2-msg",
            )
        )
        texts = [item.text for item in channel.sent[before:]]
        assert texts[:2] == ["已收到", "业务回复"]
    finally:
        orchestrator.stop()
        store.close()
        runtime.close()


def test_service_from_me_message_does_not_send_ack_or_reply(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _DummyRunner)

    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = _AckChannel()
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
                contact_id="ack3@example.com",
                chat_id="chat-ack3",
                event_id="evt-pair-ack3",
                is_from_me=False,
            )
        )
        before = len(channel.sent)
        orchestrator._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="这条来自本机",
                contact_id="ack3@example.com",
                chat_id="chat-ack3",
                event_id="evt-ack3-msg",
                is_from_me=True,
            )
        )
        assert len(channel.sent) == before
    finally:
        orchestrator.stop()
        store.close()
        runtime.close()


def test_service_slow_runner_still_fast_acks_queued_messages(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _SlowRunner)

    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = _AckChannel()
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
                contact_id="ack4@example.com",
                chat_id="chat-ack4",
                event_id="evt-pair-ack4",
            )
        )

        before = len(channel.sent)
        orchestrator._on_channel_message(
            ChannelInboundMessage(
                channel="imessage",
                text="第一条",
                contact_id="ack4@example.com",
                chat_id="chat-ack4",
                event_id="evt-ack4-msg-1",
            )
        )
        orchestrator._on_channel_message(
            ChannelInboundMessage(
                channel="imessage",
                text="第二条",
                contact_id="ack4@example.com",
                chat_id="chat-ack4",
                event_id="evt-ack4-msg-2",
            )
        )

        time.sleep(0.1)
        early_texts = [item.text for item in channel.sent[before:]]
        assert len(early_texts) >= 2
        assert early_texts[0] in {"已收到🫡", "已收到"}
        assert early_texts[1] in {"已收到🫡", "已收到"}
        assert not any(text.startswith("业务回复:") for text in early_texts[:2])

        deadline = time.time() + 3.0
        while time.time() < deadline:
            final_texts = [item.text for item in channel.sent[before:]]
            if final_texts.count("业务回复:第一条") == 1 and final_texts.count("业务回复:第二条") == 1:
                break
            time.sleep(0.05)

        outbound = [item.text for item in channel.sent[before:]]
        assert outbound[0] in {"已收到🫡", "已收到"}
        assert outbound[1] in {"已收到🫡", "已收到"}
        assert outbound[2] == "业务回复:第一条"
        assert outbound[3] == "业务回复:第二条"
    finally:
        orchestrator.stop()
        store.close()
        runtime.close()
