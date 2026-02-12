from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, List, Optional

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


class FakeChannelAdapter:
    channel_name = "imessage"

    def __init__(self) -> None:
        self.listener: Optional[Callable[[ChannelInboundMessage], None]] = None
        self.sent: List[ChannelOutboundMessage] = []
        self.health = ChannelHealthSnapshot(listener_state="running", listener_alive=True)
        self.chat_scope: Optional[str] = None
        self.pairing_probe: Optional[ChannelInboundMessage] = None
        self.polled_messages: List[ChannelInboundMessage] = []
        self.clear_polled_on_read = True
        self.telemetry_sink = None
        self.start_calls = 0
        self.stop_calls = 0

    def probe(self) -> None:
        return

    def bootstrap(self) -> ChannelBootstrapResult:
        return ChannelBootstrapResult(
            channel="imessage",
            ok=True,
            message="ok",
        )

    def start_listener(self, callback: Callable[[ChannelInboundMessage], None]) -> None:
        self.start_calls += 1
        self.listener = callback
        self.health.listener_state = "running"
        self.health.listener_alive = True

    def stop_listener(self) -> None:
        self.stop_calls += 1
        self.listener = None
        self.health.listener_state = "stopped"
        self.health.listener_alive = False

    def send_message(self, outbound: ChannelOutboundMessage) -> None:
        self.sent.append(outbound)
        self.health.raw_outbound_count += 1

    def normalize_contact_id(self, raw: str) -> str:
        return str(raw or "").strip().lower()

    def set_chat_scope(self, chat_id: Optional[str]) -> None:
        self.chat_scope = chat_id

    def set_telemetry_sink(self, sink) -> None:
        self.telemetry_sink = sink

    def poll_for_pairing_code(self, pairing_code: str, *, max_chats: int = 5):
        del pairing_code, max_chats
        return self.pairing_probe

    def poll_recent_messages(
        self,
        *,
        contact_id: str,
        chat_id: Optional[str],
        since_ts_ms: Optional[int],
        max_chats: int = 8,
        limit_per_chat: int = 8,
    ) -> List[ChannelInboundMessage]:
        del contact_id, chat_id, max_chats, limit_per_chat
        if not self.polled_messages:
            return []
        threshold = int(since_ts_ms or 0)
        items = [item for item in self.polled_messages if int(item.ts_ms or 0) >= threshold]
        if self.clear_polled_on_read:
            self.polled_messages.clear()
        return items

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
            assistant_text="PONG",
            session_id=session.session_id,
        )


def _build_orchestrator(isolated_env):
    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = FakeChannelAdapter()
    orch = ServiceOrchestrator(
        runtime=runtime,
        store=store,
        channel=channel,
        provider_id=None,
        yes=True,
    )
    return orch, runtime, store, channel


def test_pairing_and_help_command_roundtrip(isolated_env):
    orch, runtime, store, channel = _build_orchestrator(isolated_env)
    try:
        orch.start()
        code = store.get_active_pairing_code("imessage")
        assert code is not None

        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/pair {0}".format(code),
                contact_id="+8613800138000",
                chat_id="chat-a",
                event_id="evt-pair",
            )
        )

        binding = store.get_binding("imessage")
        assert binding.paired is True
        assert binding.contact_id == "+8613800138000"

        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/help",
                contact_id="+8613800138000",
                chat_id="chat-a",
                event_id="evt-help",
            )
        )

        assert channel.sent
        assert any("可用命令" in item.text for item in channel.sent)
        assert any("/service" in item.text for item in channel.sent)

        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/pair 000000",
                contact_id="+8613800138000",
                chat_id="chat-a",
                event_id="evt-pair-repeat",
            )
        )
        assert any("当前已配对" in item.text for item in channel.sent)
    finally:
        orch.stop()
        store.close()
        runtime.close()


def test_unpaired_mode_stays_poll_after_pairing(isolated_env):
    orch, runtime, store, channel = _build_orchestrator(isolated_env)
    try:
        orch.start()
        assert "ingest=poll" in orch.status_text()
        assert channel.listener is None

        code = store.get_active_pairing_code("imessage")
        assert code is not None
        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/pair {0}".format(code),
                contact_id="+8613000000000",
                chat_id="chat-switch",
                event_id="evt-pair-switch",
            )
        )

        assert "ingest=poll" in orch.status_text()
        assert channel.listener is None
    finally:
        orch.stop()
        store.close()
        runtime.close()


def test_service_start_migrates_binding_session_when_provider_mismatch(isolated_env):
    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = FakeChannelAdapter()
    try:
        old_session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            name="old-codex",
            provider_locked="codex",
            is_ephemeral=False,
        )
        store.set_binding(
            "imessage",
            contact_id="mismatch@example.com",
            chat_id="chat-mismatch",
            session_id=old_session.session_id,
        )

        orch = ServiceOrchestrator(
            runtime=runtime,
            store=store,
            channel=channel,
            provider_id="claude",
            yes=True,
        )
        orch.start()
        try:
            binding = store.get_binding("imessage")
            assert binding.session_id
            assert binding.session_id != old_session.session_id

            migrated = runtime.session_store.get_session(binding.session_id)
            assert migrated is not None
            assert migrated.provider_locked == "claude"

            still_old = runtime.session_store.get_session(old_session.session_id)
            assert still_old is not None
            assert still_old.provider_locked == "codex"
        finally:
            orch.stop()
    finally:
        store.close()
        runtime.close()


def test_plain_message_goes_to_runner_and_replies(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _DummyRunner)

    orch, runtime, store, channel = _build_orchestrator(isolated_env)
    try:
        orch.start()
        code = store.get_active_pairing_code("imessage")
        assert code is not None

        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/pair {0}".format(code),
                contact_id="user@example.com",
                chat_id="chat-b",
                event_id="evt-pair2",
            )
        )

        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="你好",
                contact_id="user@example.com",
                chat_id="chat-b",
                event_id="evt-msg",
            )
        )

        texts = [item.text for item in channel.sent]
        assert "已收到🫡" in texts
        assert any(item.text == "PONG" for item in channel.sent)
    finally:
        orch.stop()
        store.close()
        runtime.close()


def test_ack_is_sent_before_final_reply(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _DummyRunner)
    orch, runtime, store, channel = _build_orchestrator(isolated_env)
    try:
        orch.start()
        code = store.get_active_pairing_code("imessage")
        assert code is not None
        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/pair {0}".format(code),
                contact_id="order@example.com",
                chat_id="chat-order",
                event_id="evt-pair-order",
            )
        )
        before = len(channel.sent)
        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="测试顺序",
                contact_id="order@example.com",
                chat_id="chat-order",
                event_id="evt-order",
            )
        )
        outbound = [item.text for item in channel.sent[before:]]
        assert outbound[:2] == ["已收到🫡", "PONG"]
    finally:
        orch.stop()
        store.close()
        runtime.close()


def test_poll_mode_does_not_restart_watch_listener(isolated_env):
    orch, runtime, store, channel = _build_orchestrator(isolated_env)
    try:
        orch.start()
        code = store.get_active_pairing_code("imessage")
        assert code is not None
        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/pair {0}".format(code),
                contact_id="reconnect@example.com",
                chat_id="chat-reconnect",
                event_id="evt-pair-reconnect",
            )
        )

        start_before = channel.start_calls
        channel.health.listener_state = "error"
        channel.health.listener_alive = False
        time.sleep(1.6)
        assert channel.start_calls == start_before
    finally:
        orch.stop()
        store.close()
        runtime.close()


def test_paired_poll_fallback_handles_inbound_when_watch_silent(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _DummyRunner)
    orch, runtime, store, channel = _build_orchestrator(isolated_env)
    try:
        orch.start()
        code = store.get_active_pairing_code("imessage")
        assert code is not None
        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/pair {0}".format(code),
                contact_id="poll@example.com",
                chat_id="chat-poll",
                event_id="evt-pair-poll",
            )
        )

        before = len(channel.sent)
        channel.polled_messages.append(
            ChannelInboundMessage(
                channel="imessage",
                text="测试一下回复",
                contact_id="poll@example.com",
                chat_id="chat-random",
                event_id="evt-polled-inbound",
            )
        )
        time.sleep(1.2)

        outbound = [item.text for item in channel.sent[before:]]
        assert "已收到🫡" in outbound or "已收到" in outbound
        assert "PONG" in outbound
    finally:
        orch.stop()
        store.close()
        runtime.close()


def test_restart_does_not_replay_history_before_service_start(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _DummyRunner)
    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    store = ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")
    channel = FakeChannelAdapter()
    try:
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            name="prebound",
            is_ephemeral=False,
        )
        store.set_binding(
            "imessage",
            contact_id="prebound@example.com",
            chat_id="chat-prebound",
            session_id=session.session_id,
        )
        orch = ServiceOrchestrator(
            runtime=runtime,
            store=store,
            channel=channel,
            provider_id=None,
            yes=True,
        )
        orch.start()
        try:
            start_ms = orch._service_started_at_ms
            old_msg = ChannelInboundMessage(
                channel="imessage",
                text="历史旧消息",
                contact_id="prebound@example.com",
                chat_id="chat-prebound",
                event_id="evt-old-history",
                ts_ms=start_ms - 5000,
            )
            new_msg = ChannelInboundMessage(
                channel="imessage",
                text="启动后新消息",
                contact_id="prebound@example.com",
                chat_id="chat-prebound",
                event_id="evt-new-history",
                ts_ms=start_ms + 1000,
            )
            before = len(channel.sent)
            channel.polled_messages.extend([old_msg, new_msg])
            time.sleep(1.2)
            sent_texts = [item.text for item in channel.sent[before:]]
            assert "历史旧消息" not in sent_texts
            assert sent_texts.count("PONG") == 1
        finally:
            orch.stop()
    finally:
        store.close()
        runtime.close()


def test_poll_since_watermark_prevents_repeated_poll_replay(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _DummyRunner)
    orch, runtime, store, channel = _build_orchestrator(isolated_env)
    events = []
    orch.set_event_sink(lambda event: events.append(event))
    channel.clear_polled_on_read = False
    try:
        orch.start()
        code = store.get_active_pairing_code("imessage")
        assert code is not None
        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/pair {0}".format(code),
                contact_id="watermark@example.com",
                chat_id="chat-watermark",
                event_id="evt-pair-watermark",
                is_from_me=False,
            )
        )
        start_ms = orch._service_started_at_ms
        channel.polled_messages.append(
            ChannelInboundMessage(
                channel="imessage",
                text="只应处理一次",
                contact_id="watermark@example.com",
                chat_id="chat-watermark",
                event_id="evt-watermark-msg",
                ts_ms=start_ms + 1000,
                is_from_me=False,
            )
        )
        time.sleep(1.4)
        first_count = len([item for item in channel.sent if item.text == "PONG"])
        assert first_count == 1

        # Keep waiting; same poll source still returns the same message,
        # but watermark should prevent replay.
        time.sleep(1.2)
        second_count = len([item for item in channel.sent if item.text == "PONG"])
        assert second_count == 1
        assert not any(event.meta.get("reason") == "duplicate_event" for event in events)
    finally:
        orch.stop()
        store.close()
        runtime.close()


def test_status_snapshot_exposes_pairing_and_health(isolated_env):
    orch, runtime, store, channel = _build_orchestrator(isolated_env)
    try:
        orch.start()
        code = store.get_active_pairing_code("imessage")
        assert code is not None

        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/pair {0}".format(code),
                contact_id="alice@example.com",
                chat_id="chat-status",
                event_id="evt-pair-status",
            )
        )

        snapshot = orch.status_snapshot()
        assert snapshot.paired is True
        assert snapshot.contact_id == "alice@example.com"
        assert snapshot.chat_id == "chat-status"
        assert snapshot.health.listener_state in {"stopped", "running", "error"}
        assert channel.chat_scope is None

        line = orch.status_text()
        assert "paired=yes" in line
        assert "listen=poll/up" in line

        orch.unpair()
        assert channel.chat_scope is None
    finally:
        orch.stop()
        store.close()
        runtime.close()


def test_status_text_shows_acp_progress_when_active(isolated_env):
    orch, runtime, store, _channel = _build_orchestrator(isolated_env)
    try:
        orch.start()
        runtime._acp_activity.update(
            {
                "provider_id": "claude",
                "method": "session/prompt",
                "stage": "session/prompt",
                "session_id": "acp_sess_1",
                "run_id": "run_x",
                "attempt": 1,
                "elapsed_ms": 12000,
                "updated_at_ms": orchestrator_module.now_ms(),
            }
        )
        line = orch.status_text()
        assert "acp=session/prompt@12s#a1" in line
    finally:
        orch.stop()
        store.close()
        runtime.close()


def test_service_rebind_command_does_not_deadlock(isolated_env):
    orch, runtime, store, channel = _build_orchestrator(isolated_env)
    try:
        orch.start()
        code_before = store.get_active_pairing_code("imessage")
        assert code_before is not None

        output = orch.execute_local_command("/service rebind")
        assert "已重置配对" in output

        code_after = store.get_active_pairing_code("imessage")
        assert code_after is not None
        assert code_after != code_before
        assert channel.chat_scope is None
    finally:
        orch.stop()
        store.close()
        runtime.close()


def test_service_rebind_does_not_emit_duplicate_system_event(isolated_env):
    orch, runtime, store, _channel = _build_orchestrator(isolated_env)
    events = []
    orch.set_event_sink(lambda event: events.append(event))
    try:
        orch.start()
        events.clear()
        message = orch.rebind()
        assert "已重置配对" in message
        assert not any(
            event.kind == "system" and "已重置配对" in event.text
            for event in events
        )
    finally:
        orch.stop()
        store.close()
        runtime.close()


def test_from_me_message_is_ignored(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _DummyRunner)
    orch, runtime, store, channel = _build_orchestrator(isolated_env)
    events = []
    orch.set_event_sink(lambda event: events.append(event))
    try:
        orch.start()
        code = store.get_active_pairing_code("imessage")
        assert code is not None

        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/pair {0}".format(code),
                contact_id="self@example.com",
                chat_id="chat-self",
                event_id="evt-pair-self",
                is_from_me=False,
            )
        )

        before = len(channel.sent)
        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="这条是手机发来的测试消息",
                contact_id="self@example.com",
                chat_id="chat-self",
                event_id="evt-self-msg",
                is_from_me=True,
            )
        )

        assert len(channel.sent) == before
        assert any(event.meta.get("reason") == "from_me" for event in events)
    finally:
        orch.stop()
        store.close()
        runtime.close()


def test_same_text_as_outbound_still_processed_when_from_remote(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _DummyRunner)
    orch, runtime, store, channel = _build_orchestrator(isolated_env)
    try:
        orch.start()
        code = store.get_active_pairing_code("imessage")
        assert code is not None

        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/pair {0}".format(code),
                contact_id="loop@example.com",
                chat_id="chat-loop",
                event_id="evt-pair-loop",
            )
        )

        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="你好",
                contact_id="loop@example.com",
                chat_id="chat-loop",
                event_id="evt-msg-loop-1",
            )
        )
        sent_before_echo = len(channel.sent)
        assert channel.sent[-1].text == "PONG"

        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="PONG",
                contact_id="loop@example.com",
                chat_id="chat-loop",
                event_id="evt-msg-loop-echo",
                is_from_me=False,
            )
        )
        outbound = [item.text for item in channel.sent[sent_before_echo:]]
        assert outbound[:2] == ["已收到🫡", "PONG"]
    finally:
        orch.stop()
        store.close()
        runtime.close()


def test_duplicate_event_id_is_ignored_with_telemetry(isolated_env, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Runner", _DummyRunner)
    orch, runtime, store, channel = _build_orchestrator(isolated_env)
    events = []
    orch.set_event_sink(lambda event: events.append(event))
    try:
        orch.start()
        code = store.get_active_pairing_code("imessage")
        assert code is not None

        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="/pair {0}".format(code),
                contact_id="dup@example.com",
                chat_id="chat-dup",
                event_id="evt-pair-dup",
                is_from_me=False,
            )
        )

        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="第一次消息",
                contact_id="dup@example.com",
                chat_id="chat-dup",
                event_id="evt-dup-msg",
                is_from_me=False,
            )
        )
        before = len(channel.sent)
        orch._process_inbound(
            ChannelInboundMessage(
                channel="imessage",
                text="第二次重复消息",
                contact_id="dup@example.com",
                chat_id="chat-dup",
                event_id="evt-dup-msg",
                is_from_me=False,
            )
        )

        assert len(channel.sent) == before
        assert any(event.meta.get("reason") == "duplicate_event" for event in events)
    finally:
        orch.stop()
        store.close()
        runtime.close()
