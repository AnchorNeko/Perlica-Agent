"""Channel-agnostic service orchestrator for bridge mode."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Deque
from typing import Callable, Optional

from perlica.kernel.policy_engine import ApprovalAction
from perlica.kernel.runner import Runner
from perlica.kernel.runtime import Runtime
from perlica.kernel.session_store import SessionRecord
from perlica.kernel.types import ToolCall, now_ms
from perlica.providers.base import ProviderError, provider_error_summary
from perlica.repl_commands import (
    InteractionCommandHooks,
    ReplState,
    ServiceCommandHooks,
    execute_slash_command_to_text,
)
from perlica.service.channels.base import ChannelAdapter
from perlica.service.store import ServiceStore
from perlica.service.tool_policies import apply_tool_policy, list_tool_policy_lines
from perlica.service.types import (
    ChannelHealthSnapshot,
    ChannelInboundMessage,
    ChannelOutboundMessage,
    ChannelTelemetryEvent,
    PairingState,
    ServiceEvent,
    ServiceStatusSnapshot,
)
from perlica.ui.render import render_notice, render_repl_help_summary

ServiceEventSink = Callable[[ServiceEvent], None]


class ServiceOrchestrator:
    """Owns pairing, inbound routing, and response dispatch for one channel."""

    def __init__(
        self,
        runtime: Runtime,
        store: ServiceStore,
        channel: ChannelAdapter,
        provider_id: Optional[str],
        yes: bool,
        event_sink: Optional[ServiceEventSink] = None,
    ) -> None:
        self._runtime = runtime
        self._store = store
        self._channel = channel
        normalized_provider = str(provider_id or "").strip().lower()
        self._provider_id = normalized_provider or None
        self._yes = yes
        self._event_sink = event_sink

        self._binding = self._store.get_binding(self._channel.channel_name)
        self._pair_code: Optional[str] = None
        self._received_bound_messages = 0
        self._ignored_messages = 0
        self._last_bound_inbound_at_ms: Optional[int] = None
        self._recent_inbound_event_ids: Deque[str] = deque(maxlen=256)
        self._recent_inbound_event_id_set = set()
        self._acked_inbound_event_ids: Deque[str] = deque(maxlen=512)
        self._acked_inbound_event_id_set = set()
        self._queue_depth = 0
        self._queue_max_depth = 0
        self._queue_busy = False
        self._queue_depth_last_reported = 0
        self._pending_announced_interaction_id = ""
        self._service_started_at_ms = now_ms()
        self._poll_since_ts_ms = self._service_started_at_ms

        self._state = ReplState(
            context_id=self._runtime.context_id,
            provider=self._provider_id,
            yes=self._yes,
            session_ref=self._binding.session_id,
            session_name=None,
            session_is_ephemeral=False,
            service_hooks=ServiceCommandHooks(
                status=self.status_text,
                rebind=self.rebind,
                unpair=self.unpair,
                channel_list=self._channel_list_text,
                channel_use=self._channel_use_text,
                channel_current=self._channel_current_text,
                tools_list=self._tools_list_text,
                tools_allow=self._tools_allow_text,
                tools_deny=self._tools_deny_text,
            ),
            interaction_hooks=InteractionCommandHooks(
                pending=self.pending_interaction_text,
                choose=self.submit_interaction_answer,
                has_pending=self.has_pending_interaction,
                choice_suggestions=self.pending_choice_suggestions,
            ),
        )

        self._queue: "queue.Queue[ChannelInboundMessage]" = queue.Queue()
        # Service slash commands may call back into orchestrator methods that
        # also need locking (e.g. /service rebind). Use reentrant lock to avoid
        # self-deadlock in the same thread.
        self._run_lock = threading.RLock()
        self._worker_thread: Optional[threading.Thread] = None
        self._pending_watch_thread: Optional[threading.Thread] = None
        self._pair_poll_thread: Optional[threading.Thread] = None
        self._pair_poll_stop = threading.Event()
        self._running = False

    @property
    def channel_name(self) -> str:
        return self._channel.channel_name

    @property
    def state(self) -> ReplState:
        return self._state

    def set_event_sink(self, sink: Optional[ServiceEventSink]) -> None:
        self._event_sink = sink

    def start(self) -> None:
        if self._running:
            return

        telemetry_setter = getattr(self._channel, "set_telemetry_sink", None)
        if callable(telemetry_setter):
            telemetry_setter(self._on_channel_telemetry)

        self._channel.probe()
        self._service_started_at_ms = now_ms()
        self._poll_since_ts_ms = self._service_started_at_ms
        self._queue_depth = 0
        self._queue_max_depth = 0
        self._queue_busy = False
        self._queue_depth_last_reported = 0
        self._running = True
        with self._run_lock:
            self._bootstrap_binding_state_locked()
            self._ensure_ingest_mode_locked()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="perlica-service-orchestrator",
        )
        self._worker_thread.start()
        self._pending_watch_thread = threading.Thread(
            target=self._pending_watch_loop,
            daemon=True,
            name="perlica-service-pending-watch",
        )
        self._pending_watch_thread.start()

        self._emit(
            "system",
            "Service 模式已启动：渠道={0}".format(self._channel.channel_name),
        )

    def stop(self) -> None:
        self._running = False
        self._stop_pairing_poller_locked()
        self._channel.stop_listener()
        telemetry_setter = getattr(self._channel, "set_telemetry_sink", None)
        if callable(telemetry_setter):
            telemetry_setter(None)
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3)
        self._worker_thread = None
        if self._pending_watch_thread and self._pending_watch_thread.is_alive():
            self._pending_watch_thread.join(timeout=3)
        self._pending_watch_thread = None
        self._queue_busy = False
        self._queue_depth = 0

    def status_text(self) -> str:
        snapshot = self.status_snapshot()
        health = snapshot.health
        paired_text = "yes" if snapshot.paired else "no"
        contact = snapshot.contact_id or "-"
        last_in = _format_age(snapshot.last_bound_inbound_at_ms)
        raw_in = health.raw_inbound_count
        raw_out = health.raw_outbound_count
        raw_lines = health.raw_line_count
        last_err = health.last_error or "-"
        if len(last_err) > 60:
            last_err = last_err[:57] + "..."
        if snapshot.paired:
            pair_hint = "-"
        else:
            pair_hint = snapshot.pairing_code or "-"
        ingest = "poll"
        poller_alive = bool(self._pair_poll_thread and self._pair_poll_thread.is_alive())
        listen = "{0}/{1}".format("poll", "up" if poller_alive else "down")
        acp_status = self._acp_status_text()

        return (
            "ch={0} paired={1} ingest={2} contact={3} chat={4} session={5} listen={6} "
            "raw_lines={7} raw_in={8} raw_out={9} bound_in={10} last_in={11} "
            "pair_code={12} queue={13} queue_max={14} busy={15} last_err={16} acp={17}"
        ).format(
            snapshot.channel,
            paired_text,
            ingest,
            contact,
                snapshot.chat_id or "-",
                snapshot.session_id or "-",
                listen,
            raw_lines,
            raw_in,
            raw_out,
            snapshot.received_bound_messages,
            last_in,
            pair_hint,
            snapshot.queue_depth,
            snapshot.queue_max_depth,
            "yes" if snapshot.queue_busy else "no",
            last_err,
            acp_status,
        )

    def _acp_status_text(self) -> str:
        if self._runtime.settings.provider_backend != "acp":
            return "-"
        snapshot = self._runtime.acp_activity_snapshot()
        if not isinstance(snapshot, dict):
            return "-"
        provider_id = str(snapshot.get("provider_id") or "")
        if self._provider_id and provider_id and provider_id != self._provider_id:
            return "-"
        age_ms = int(snapshot.get("age_ms") or 0)
        if age_ms > 4000:
            return "-"
        stage = str(snapshot.get("stage") or snapshot.get("method") or "session/prompt")
        elapsed_ms = int(snapshot.get("elapsed_ms") or 0)
        elapsed_sec = max(0, int(elapsed_ms / 1000))
        attempt = int(snapshot.get("attempt") or 0)
        return "{0}@{1}s#a{2}".format(stage, elapsed_sec, attempt)

    def status_snapshot(self) -> ServiceStatusSnapshot:
        health = self._channel_health_snapshot()
        pair_code = None
        if not self._binding.paired:
            pair_code = self._pair_code or self._store.get_active_pairing_code(self._binding.channel)
        return ServiceStatusSnapshot(
            channel=self._binding.channel,
            paired=self._binding.paired,
            contact_id=self._binding.contact_id,
            chat_id=self._binding.chat_id,
            session_id=self._binding.session_id,
            pairing_code=pair_code,
            received_bound_messages=self._received_bound_messages,
            ignored_messages=self._ignored_messages,
            last_bound_inbound_at_ms=self._last_bound_inbound_at_ms,
            queue_depth=self._queue_depth,
            queue_max_depth=self._queue_max_depth,
            queue_busy=self._queue_busy,
            health=health,
        )

    def rebind(self) -> str:
        with self._run_lock:
            self._binding = self._store.clear_binding(self._channel.channel_name)
            self._pair_code = self._store.create_pairing_code(self._channel.channel_name)
            self._poll_since_ts_ms = now_ms()
            self._apply_channel_scope_locked()
            self._ensure_ingest_mode_locked()
        message = render_notice(
            "success",
            "已重置配对，请手机发送 `/pair {0}`。".format(self._pair_code),
            "Binding reset. Send `/pair {0}` from mobile.".format(self._pair_code),
        )
        return message

    def unpair(self) -> str:
        with self._run_lock:
            self._binding = self._store.clear_binding(self._channel.channel_name)
            self._pair_code = self._store.create_pairing_code(self._channel.channel_name)
            self._poll_since_ts_ms = now_ms()
            self._apply_channel_scope_locked()
            self._ensure_ingest_mode_locked()
        message = render_notice(
            "success",
            "已解除配对。后续仅接受 `/pair {0}`。".format(self._pair_code),
            "Unpaired. Only `/pair {0}` is accepted now.".format(self._pair_code),
        )
        return message

    def execute_local_command(self, raw_line: str) -> str:
        with self._run_lock:
            return self._execute_slash_locked(raw_line)

    def execute_local_text(self, text: str) -> str:
        with self._run_lock:
            return self._run_model_locked(text)

    def _bootstrap_binding_state_locked(self) -> None:
        self._binding = self._store.get_binding(self._channel.channel_name)
        self._apply_channel_scope_locked()

        if self._binding.paired and self._binding.contact_id:
            session = self._ensure_session_locked(self._binding.session_id)
            self._binding = self._store.set_binding(
                self._channel.channel_name,
                contact_id=self._binding.contact_id,
                chat_id=self._binding.chat_id,
                session_id=session.session_id,
            )
            self._state.session_ref = session.session_id
            self._state.session_name = session.name
            self._state.session_is_ephemeral = session.is_ephemeral
            self._pair_code = None
            # Prevent replaying old history after service restart.
            self._poll_since_ts_ms = self._service_started_at_ms
            self._apply_channel_scope_locked()
            self._emit(
                "system",
                "已配对联系人：{0}（chat={1}） session={2}".format(
                    self._binding.contact_id,
                    self._binding.chat_id or "",
                    self._binding.session_id or "",
                ),
            )
            return

        active_code = self._store.get_active_pairing_code(self._channel.channel_name)
        self._pair_code = active_code or self._store.create_pairing_code(self._channel.channel_name)
        self._poll_since_ts_ms = self._service_started_at_ms
        self._apply_channel_scope_locked()
        self._emit(
            "system",
            "未配对，请在手机发送 `/pair {0}`。".format(self._pair_code),
        )

    def _on_channel_message(self, inbound: ChannelInboundMessage) -> None:
        if not self._running:
            return
        if self._try_submit_pending_answer_fast(inbound, source="remote"):
            return
        with self._run_lock:
            if self._try_submit_pending_answer_locked(inbound, source="remote"):
                return
            self._maybe_send_fast_ack_locked(inbound)
        self._queue.put(inbound)
        self._queue_depth = self._queue.qsize()
        if self._queue_depth > self._queue_max_depth:
            self._queue_max_depth = self._queue_depth
        if self._queue_depth > 1 and self._queue_depth != self._queue_depth_last_reported:
            self._queue_depth_last_reported = self._queue_depth
            self._emit(
                "telemetry",
                "入站消息排队中，queue_depth={0}".format(self._queue_depth),
                contact_id=inbound.contact_id,
                chat_id=inbound.chat_id,
                meta={
                    "event_type": "inbound.queued",
                    "direction": "inbound",
                    "queue_depth": self._queue_depth,
                },
            )

    def _on_channel_telemetry(self, telemetry: ChannelTelemetryEvent) -> None:
        self._emit(
            "telemetry",
            telemetry.text,
            meta={
                "event_type": telemetry.event_type,
                "direction": telemetry.direction,
                "payload": dict(telemetry.payload or {}),
            },
        )

    def _worker_loop(self) -> None:
        while self._running:
            try:
                inbound = self._queue.get(timeout=0.2)
            except queue.Empty:
                with self._run_lock:
                    self._maybe_announce_pending_interaction_locked()
                continue

            try:
                self._queue_busy = True
                self._queue_depth = self._queue.qsize()
                self._process_inbound(inbound)
                self._maybe_announce_pending_interaction_locked()
            except Exception as exc:  # pragma: no cover - defensive
                self._emit(
                    "error",
                    render_notice(
                        "error",
                        "Service 处理失败：{0}".format(exc),
                        "Service processing failed: {0}".format(exc),
                    ),
                )
            finally:
                self._queue_busy = False
                self._queue_depth = self._queue.qsize()
                if self._queue_depth <= 1:
                    self._queue_depth_last_reported = self._queue_depth

    def _process_inbound(self, inbound: ChannelInboundMessage) -> None:
        with self._run_lock:
            dedupe_key = self._inbound_dedupe_key(inbound)
            if dedupe_key:
                if self._is_duplicate_inbound_event_id(dedupe_key):
                    self._ignored_messages += 1
                    self._emit(
                        "telemetry",
                        "忽略重复事件。",
                        contact_id=inbound.contact_id,
                        chat_id=inbound.chat_id,
                        meta={
                            "event_type": "inbound.ignored",
                            "direction": "inbound",
                            "reason": "duplicate_event",
                            "event_id": inbound.event_id,
                            "dedupe_key": dedupe_key,
                        },
                    )
                    return
                self._mark_inbound_event_id(dedupe_key)

            if inbound.is_from_me:
                self._ignored_messages += 1
                self._emit(
                    "telemetry",
                    "忽略本机发送消息。",
                    contact_id=inbound.contact_id,
                    chat_id=inbound.chat_id,
                    meta={
                        "event_type": "inbound.ignored",
                        "direction": "inbound",
                        "reason": "from_me",
                        "event_id": inbound.event_id,
                    },
                )
                return

            if inbound.event_id:
                # Cursor is only advanced for remote inbound events to avoid
                # shadowing a same-id remote event behind a local echo variant.
                self._store.set_cursor(self._channel.channel_name, inbound.event_id)

            stripped = inbound.text.strip()
            if stripped.startswith("/pair") and self._binding.paired and self._is_bound_sender(inbound):
                self._send_message_locked(
                    contact_id=inbound.contact_id,
                    chat_id=inbound.chat_id,
                    text=render_notice(
                        "info",
                        "当前已配对。可执行 `/service rebind` 重新绑定。",
                        "Already paired. Use `/service rebind` to rebind.",
                    ),
                )
                return

            if not self._is_bound_sender(inbound):
                if self._binding.paired:
                    self._ignored_messages += 1
                    self._emit(
                        "telemetry",
                        "忽略非绑定联系人消息。",
                        contact_id=inbound.contact_id,
                        chat_id=inbound.chat_id,
                        meta={
                            "event_type": "inbound.ignored",
                            "direction": "inbound",
                            "reason": "contact_mismatch",
                            "bound_contact": self._binding.contact_id or "",
                            "inbound_contact": self._channel.normalize_contact_id(inbound.contact_id),
                        },
                    )
                    return
                self._process_pairing_message_locked(inbound)
                return

            self._received_bound_messages += 1
            self._last_bound_inbound_at_ms = inbound.ts_ms or None
            self._emit(
                "inbound",
                inbound.text,
                contact_id=inbound.contact_id,
                chat_id=inbound.chat_id,
            )
            self._send_ack_locked(inbound)

            if inbound.text.strip().startswith("/"):
                reply = self._execute_slash_locked(inbound.text)
            else:
                reply = self._run_model_locked(inbound.text)

            self._send_reply_locked(reply, purpose="reply")

    def has_pending_interaction(self) -> bool:
        return self._runtime.interaction_coordinator.has_pending()

    def pending_interaction_text(self) -> str:
        return self._runtime.interaction_coordinator.pending_hint_text()

    def pending_choice_suggestions(self) -> list[str]:
        return self._runtime.interaction_coordinator.choice_suggestions()

    def submit_interaction_answer(self, raw_input: str, source: str) -> str:
        result = self._runtime.interaction_coordinator.submit_answer(raw_input, source=source)
        if result.accepted:
            return render_notice(
                "success",
                result.message,
                "Interaction answer submitted.",
            )
        return render_notice(
            "warn",
            result.message,
            "Interaction answer rejected.",
        )

    def _try_submit_pending_answer_locked(self, inbound: ChannelInboundMessage, source: str) -> bool:
        if not self.has_pending_interaction():
            return False
        if inbound.is_from_me:
            return False
        if not self._is_bound_sender(inbound):
            return False

        stripped = inbound.text.strip()
        if not stripped:
            return False
        if stripped.startswith("/"):
            return False

        result = self._runtime.interaction_coordinator.submit_answer(stripped, source=source)
        if result.accepted:
            self._received_bound_messages += 1
            self._last_bound_inbound_at_ms = inbound.ts_ms or None
            self._emit(
                "inbound",
                inbound.text,
                contact_id=inbound.contact_id,
                chat_id=inbound.chat_id,
                meta={
                    "event_type": "interaction.answer.inbound",
                    "direction": "inbound",
                    "source": source,
                },
            )
            self._send_ack_locked(inbound)
            self._send_reply_locked(
                render_notice(
                    "success",
                    "交互回答已提交，继续执行中。",
                    "Interaction answer submitted. Continuing execution.",
                ),
                purpose="reply",
            )
            return True

        self._send_reply_locked(
            render_notice(
                "warn",
                result.message,
                "Interaction answer rejected.",
            ),
            purpose="reply",
        )
        self._emit(
            "telemetry",
            "交互回答被拒绝：{0}".format(result.message),
            contact_id=inbound.contact_id,
            chat_id=inbound.chat_id,
            meta={
                "event_type": "interaction.answer_rejected",
                "direction": "inbound",
                "source": source,
            },
        )
        return True

    def _try_submit_pending_answer_fast(self, inbound: ChannelInboundMessage, source: str) -> bool:
        stripped = inbound.text.strip()
        if not stripped:
            return False

        is_choose_command = stripped.startswith("/choose")
        is_pending_command = stripped == "/pending"

        if not self._binding.paired or not self._binding.contact_id:
            return False
        inbound_contact = self._channel.normalize_contact_id(inbound.contact_id)
        bound_contact = str(self._binding.contact_id or "").strip()
        same_contact = inbound_contact == bound_contact
        same_chat = bool(
            self._binding.chat_id
            and inbound.chat_id
            and str(inbound.chat_id).strip() == str(self._binding.chat_id).strip()
        )
        allow_same_chat_override = bool(inbound.is_from_me and (is_choose_command or is_pending_command) and same_chat)
        if not same_contact and not allow_same_chat_override:
            return False

        allow_from_me_override = bool(inbound.is_from_me and (is_choose_command or is_pending_command))
        if inbound.is_from_me and not allow_from_me_override:
            return False

        # Remote /pending should be responsive even while worker thread is busy.
        if is_pending_command:
            self._send_reply_locked(self.pending_interaction_text(), purpose="reply")
            if allow_from_me_override:
                self._emit(
                    "telemetry",
                    "from_me 消息通过快速通道处理（pending）。",
                    contact_id=inbound.contact_id,
                    chat_id=inbound.chat_id,
                    meta={
                        "event_type": "interaction.answer.from_me_override",
                        "direction": "inbound",
                        "source": source,
                        "raw_input": stripped,
                        "same_chat_override": bool(allow_same_chat_override),
                    },
                )
            return True

        if not self.has_pending_interaction():
            return False

        answer_text = stripped
        if is_choose_command:
            answer_text = stripped[len("/choose") :].strip()
            if not answer_text:
                self._send_reply_locked(
                    render_notice(
                        "warn",
                        "请输入 `/choose <编号|文本>`。",
                        "Use `/choose <index|text>`.",
                    ),
                    purpose="reply",
                )
                return True
        elif stripped.startswith("/"):
            # Keep other slash commands on normal service command path.
            return False

        result = self._runtime.interaction_coordinator.submit_answer(answer_text, source=source)
        if result.accepted:
            self._received_bound_messages += 1
            self._last_bound_inbound_at_ms = inbound.ts_ms or None
            self._emit(
                "inbound",
                inbound.text,
                contact_id=inbound.contact_id,
                chat_id=inbound.chat_id,
                meta={
                    "event_type": "interaction.answer.inbound",
                    "direction": "inbound",
                    "source": source,
                    "path": "fast",
                    "raw_input": stripped,
                },
            )
            self._send_ack_locked(inbound)
            self._send_reply_locked(
                render_notice(
                    "success",
                    "交互回答已提交，继续执行中。",
                    "Interaction answer submitted. Continuing execution.",
                ),
                purpose="reply",
            )
            self._emit(
                "telemetry",
                "快速通道已提交交互回答。",
                contact_id=inbound.contact_id,
                chat_id=inbound.chat_id,
                meta={
                    "event_type": "interaction.answer.fast_submitted",
                    "direction": "inbound",
                    "source": source,
                    "raw_input": stripped,
                    "answer_text": answer_text,
                    "from_me_override": bool(allow_from_me_override),
                    "same_chat_override": bool(allow_same_chat_override),
                },
            )
            return True
        return False

    def _maybe_announce_pending_interaction_locked(self) -> None:
        snapshot = self._runtime.interaction_coordinator.snapshot()
        if not snapshot.has_pending:
            self._pending_announced_interaction_id = ""
            return
        interaction_id = str(snapshot.interaction_id or "").strip()
        if not interaction_id:
            return
        if interaction_id == self._pending_announced_interaction_id:
            return
        if not self._binding.paired or not self._binding.contact_id:
            return

        pending_text = self.pending_interaction_text().strip()
        if not pending_text:
            return

        message = "检测到待确认交互，请直接回复编号或文本：\n{0}".format(pending_text)
        self._send_message_locked(
            contact_id=self._binding.contact_id,
            chat_id=self._binding.chat_id,
            text=message,
            purpose="interaction_prompt",
        )
        self._emit(
            "telemetry",
            "已将待确认交互推送到远端联系人。",
            contact_id=self._binding.contact_id,
            chat_id=self._binding.chat_id,
            meta={
                "event_type": "interaction.prompt.forwarded",
                "direction": "outbound",
                "interaction_id": interaction_id,
            },
        )
        self._pending_announced_interaction_id = interaction_id

    def _pending_watch_loop(self) -> None:
        while self._running:
            try:
                self._maybe_announce_pending_interaction_nolock()
            except Exception:
                # Best-effort watcher; do not break service loop.
                pass
            time.sleep(0.2)

    def _maybe_announce_pending_interaction_nolock(self) -> None:
        snapshot = self._runtime.interaction_coordinator.snapshot()
        if not snapshot.has_pending:
            self._pending_announced_interaction_id = ""
            return
        interaction_id = str(snapshot.interaction_id or "").strip()
        if not interaction_id:
            return
        if interaction_id == self._pending_announced_interaction_id:
            return
        if not self._binding.paired or not self._binding.contact_id:
            return

        pending_text = self.pending_interaction_text().strip()
        if not pending_text:
            return

        message = "检测到待确认交互，请直接回复编号或文本：\n{0}".format(pending_text)
        self._send_message_locked(
            contact_id=self._binding.contact_id,
            chat_id=self._binding.chat_id,
            text=message,
            purpose="interaction_prompt",
        )
        self._emit(
            "telemetry",
            "已将待确认交互推送到远端联系人。",
            contact_id=self._binding.contact_id,
            chat_id=self._binding.chat_id,
            meta={
                "event_type": "interaction.prompt.forwarded",
                "direction": "outbound",
                "interaction_id": interaction_id,
            },
        )
        self._pending_announced_interaction_id = interaction_id

    def _process_pairing_message_locked(self, inbound: ChannelInboundMessage) -> None:
        stripped = inbound.text.strip()
        if not stripped.startswith("/pair"):
            self._send_message_locked(
                contact_id=inbound.contact_id,
                chat_id=inbound.chat_id,
                text=render_notice(
                    "info",
                    "尚未配对，请发送 `/pair <code>`。",
                    "Not paired yet. Send `/pair <code>`.",
                ),
            )
            return

        parts = stripped.split()
        code = parts[1] if len(parts) > 1 else ""
        if not self._store.consume_pairing_code(self._channel.channel_name, code):
            self._send_message_locked(
                contact_id=inbound.contact_id,
                chat_id=inbound.chat_id,
                text=render_notice(
                    "error",
                    "配对码无效或已过期，请在电脑端执行 `/service rebind` 获取新码。",
                    "Invalid/expired code. Run `/service rebind` on desktop.",
                ),
            )
            return

        session = self._ensure_session_locked(self._binding.session_id)
        self._binding = self._store.set_binding(
            self._channel.channel_name,
            contact_id=self._channel.normalize_contact_id(inbound.contact_id),
            chat_id=inbound.chat_id,
            session_id=session.session_id,
        )
        self._state.session_ref = session.session_id
        self._state.session_name = session.name
        self._state.session_is_ephemeral = session.is_ephemeral
        self._pair_code = None
        self._poll_since_ts_ms = inbound.ts_ms if inbound.ts_ms else now_ms()
        self._apply_channel_scope_locked()
        self._ensure_ingest_mode_locked()

        self._emit(
            "system",
            "配对成功：contact={0} session={1}".format(
                self._binding.contact_id,
                self._binding.session_id,
            ),
            contact_id=self._binding.contact_id,
            chat_id=self._binding.chat_id,
        )

        self._send_message_locked(
            contact_id=self._binding.contact_id or inbound.contact_id,
            chat_id=self._binding.chat_id,
            text="配对成功。\n" + render_repl_help_summary(),
            purpose="pairing",
        )

    def _execute_slash_locked(self, raw_line: str) -> str:
        result, output = execute_slash_command_to_text(raw_line=raw_line, state=self._state)
        self._sync_binding_session_locked()

        if result.exit_requested:
            return render_notice(
                "info",
                "Service 模式忽略退出命令。",
                "Exit command is ignored in service mode.",
            )

        if result.handled:
            return output or render_notice(
                "success",
                "命令执行完成。",
                "Command completed.",
            )

        # Unknown slash command falls back to model conversation.
        return self._run_model_locked(raw_line)

    def _run_model_locked(self, text: str) -> str:
        session = self._ensure_session_locked(self._state.session_ref)

        runner = Runner(
            runtime=self._runtime,
            provider_id=self._provider_id,
            max_tool_calls=self._runtime.settings.max_tool_calls,
            approval_resolver=self._approval_resolver(),
        )

        try:
            result = runner.run_text(
                text=text,
                assume_yes=self._yes,
                session_ref=session.session_id,
            )
        except ProviderError as exc:
            error_detail = provider_error_summary(exc)
            return render_notice(
                "error",
                "模型调用失败：{0}".format(error_detail),
                "Provider error: {0}".format(error_detail),
            )
        except Exception as exc:  # pragma: no cover - defensive
            return render_notice(
                "error",
                "执行失败：{0}".format(exc),
                "Execution failed: {0}".format(exc),
            )

        self._state.session_ref = result.session_id
        self._sync_binding_session_locked()
        return result.assistant_text or render_notice("info", "模型返回空结果。", "Model returned empty output.")

    def _send_ack_locked(self, inbound: ChannelInboundMessage) -> bool:
        if not self._binding.paired or not self._binding.contact_id:
            return False
        event_id = self._normalize_event_id(inbound.event_id)
        if event_id and self._was_ack_sent_for_event(event_id):
            return False
        contact_id = self._binding.contact_id
        chat_id = self._binding.chat_id or inbound.chat_id
        sent = False
        try:
            self._send_message_locked(
                contact_id=contact_id,
                chat_id=chat_id,
                text="已收到🫡",
                purpose="ack",
            )
            sent = True
        except Exception as exc:
            self._emit(
                "error",
                render_notice(
                    "warn",
                    "ACK 发送失败，正在尝试降级文本：{0}".format(exc),
                    "ACK send failed, retrying with fallback text: {0}".format(exc),
                ),
            )

        if not sent:
            try:
                self._send_message_locked(
                    contact_id=contact_id,
                    chat_id=chat_id,
                    text="已收到",
                    purpose="ack",
                )
                sent = True
            except Exception as exc:
                self._emit(
                    "error",
                    render_notice(
                        "error",
                        "ACK 发送失败：{0}".format(exc),
                        "ACK send failed: {0}".format(exc),
                    ),
                )

        if sent and event_id:
            self._mark_ack_sent_for_event(event_id)
        return sent

    def _maybe_send_fast_ack_locked(self, inbound: ChannelInboundMessage) -> None:
        if not self._can_send_fast_ack(inbound):
            return
        if self._send_ack_locked(inbound):
            self._emit(
                "telemetry",
                "快速 ACK 已发送。",
                contact_id=inbound.contact_id,
                chat_id=inbound.chat_id,
                meta={
                    "event_type": "ack.fast_sent",
                    "direction": "outbound",
                    "event_id": inbound.event_id or "",
                },
            )

    def _can_send_fast_ack(self, inbound: ChannelInboundMessage) -> bool:
        if not self._binding.paired or not self._binding.contact_id:
            return False
        if inbound.is_from_me:
            return False
        if not self._is_bound_sender(inbound):
            return False
        stripped = inbound.text.strip()
        if not stripped or stripped.startswith("/pair"):
            return False
        return True

    @staticmethod
    def _normalize_event_id(event_id: str) -> str:
        return str(event_id or "").strip()

    def _was_ack_sent_for_event(self, event_id: str) -> bool:
        normalized = self._normalize_event_id(event_id)
        if not normalized:
            return False
        return normalized in self._acked_inbound_event_id_set

    def _mark_ack_sent_for_event(self, event_id: str) -> None:
        normalized = self._normalize_event_id(event_id)
        if not normalized:
            return
        if normalized in self._acked_inbound_event_id_set:
            return
        if len(self._acked_inbound_event_ids) >= self._acked_inbound_event_ids.maxlen:
            oldest = self._acked_inbound_event_ids.popleft()
            self._acked_inbound_event_id_set.discard(oldest)
        self._acked_inbound_event_ids.append(normalized)
        self._acked_inbound_event_id_set.add(normalized)

    def _send_reply_locked(self, text: str, purpose: str) -> None:
        if not self._binding.paired or not self._binding.contact_id:
            return
        self._send_message_locked(
            contact_id=self._binding.contact_id,
            chat_id=self._binding.chat_id,
            text=text,
            purpose=purpose,
        )

    def _send_message_locked(
        self,
        *,
        contact_id: str,
        chat_id: Optional[str],
        text: str,
        purpose: str = "outbound",
    ) -> None:
        outbound = ChannelOutboundMessage(
            channel=self._channel.channel_name,
            text=text,
            contact_id=contact_id,
            chat_id=chat_id,
        )
        self._channel.send_message(outbound)
        kind = "outbound"
        if purpose == "ack":
            kind = "ack"
        elif purpose == "reply":
            kind = "reply"
        self._emit(
            kind,
            text,
            contact_id=contact_id,
            chat_id=chat_id,
            meta={"purpose": purpose},
        )

    def _is_bound_sender(self, inbound: ChannelInboundMessage) -> bool:
        if not self._binding.paired or not self._binding.contact_id:
            return False

        normalized = self._channel.normalize_contact_id(inbound.contact_id)
        return normalized == self._binding.contact_id

    def _channel_list_text(self) -> str:
        return "channels:\n* {0} (active)".format(self._channel.channel_name)

    def _channel_current_text(self) -> str:
        return "active_channel={0}".format(self._channel.channel_name)

    def _channel_use_text(self, channel_id: str) -> str:
        normalized = str(channel_id or "").strip().lower()
        if not normalized:
            return render_notice(
                "error",
                "请提供渠道 ID，例如 imessage。",
                "Channel id is required, e.g. imessage.",
            )
        if normalized == self._channel.channel_name:
            return render_notice(
                "info",
                "当前渠道已是 `{0}`。".format(self._channel.channel_name),
                "Channel `{0}` is already active.".format(self._channel.channel_name),
            )
        return render_notice(
            "warn",
            "远端不支持切换渠道，请在本地 TUI 执行 `/service channel use {0}`。".format(normalized),
            "Remote channel switch is disabled; use local TUI command instead.",
        )

    def _tools_list_text(self) -> str:
        return "\n".join(list_tool_policy_lines(self._runtime))

    def _tools_allow_text(
        self,
        tool_name: Optional[str],
        apply_all: bool,
        risk: Optional[str],
    ) -> str:
        try:
            report = apply_tool_policy(
                self._runtime,
                allow=True,
                tool_name=tool_name,
                apply_all=apply_all,
                risk=risk,
            )
        except ValueError as exc:
            return render_notice("error", str(exc), "Invalid tool policy command.")

        target = "全部工具" if apply_all else str(tool_name or "")
        return render_notice(
            "success",
            "已允许 {0}，risk={1}，更新 {2} 条规则。".format(
                target,
                ",".join(report["risks"]),
                report["updated"],
            ),
            "Tool(s) allowed.",
        )

    def _tools_deny_text(
        self,
        tool_name: Optional[str],
        apply_all: bool,
        risk: Optional[str],
    ) -> str:
        try:
            report = apply_tool_policy(
                self._runtime,
                allow=False,
                tool_name=tool_name,
                apply_all=apply_all,
                risk=risk,
            )
        except ValueError as exc:
            return render_notice("error", str(exc), "Invalid tool policy command.")

        target = "全部工具" if apply_all else str(tool_name or "")
        return render_notice(
            "success",
            "已禁止 {0}，risk={1}，更新 {2} 条规则。".format(
                target,
                ",".join(report["risks"]),
                report["updated"],
            ),
            "Tool(s) denied.",
        )

    def _sync_binding_session_locked(self) -> None:
        if not self._binding.paired or not self._binding.contact_id:
            return
        session_id = self._state.session_ref
        if not session_id:
            return

        if session_id == self._binding.session_id:
            return

        self._binding = self._store.set_binding(
            self._channel.channel_name,
            contact_id=self._binding.contact_id,
            chat_id=self._binding.chat_id,
            session_id=session_id,
        )

    def _ensure_session_locked(self, preferred_session_id: Optional[str]) -> SessionRecord:
        if preferred_session_id:
            existing = self._runtime.session_store.get_session(preferred_session_id)
            if existing is not None and existing.context_id == self._runtime.context_id:
                existing = self._align_session_provider_locked(existing)
                self._runtime.session_store.set_current_session(self._runtime.context_id, existing.session_id)
                return existing

        if self._state.session_ref:
            existing = self._runtime.session_store.get_session(self._state.session_ref)
            if existing is not None and existing.context_id == self._runtime.context_id:
                existing = self._align_session_provider_locked(existing)
                self._runtime.session_store.set_current_session(self._runtime.context_id, existing.session_id)
                return existing

        created = self._create_default_service_session_locked()
        self._state.session_ref = created.session_id
        self._state.session_name = created.name
        self._state.session_is_ephemeral = created.is_ephemeral
        self._runtime.session_store.set_current_session(self._runtime.context_id, created.session_id)
        return created

    def _align_session_provider_locked(self, session: SessionRecord) -> SessionRecord:
        preferred_provider = self._provider_id
        if not preferred_provider:
            return session

        locked = str(session.provider_locked or "").strip().lower()
        if locked == preferred_provider:
            return session

        if not locked:
            try:
                updated = self._runtime.session_store.lock_provider(session.session_id, preferred_provider)
            except ValueError:
                return session
            self._emit(
                "system",
                "Service 会话未锁定 provider，已锁定为 `{0}`：{1}".format(
                    preferred_provider,
                    updated.session_id,
                ),
            )
            return updated

        replacement = self._runtime.session_store.create_session(
            context_id=self._runtime.context_id,
            name=None,
            provider_locked=preferred_provider,
            is_ephemeral=False,
        )
        self._emit(
            "system",
            (
                "已检测到绑定会话 provider 冲突（session={0}, locked={1}, requested={2}），"
                "已切换到新会话：{3}"
            ).format(
                session.session_id,
                locked,
                preferred_provider,
                replacement.session_id,
            ),
        )
        return replacement

    def _create_default_service_session_locked(self) -> SessionRecord:
        default_name = "service-{0}".format(self._channel.channel_name)

        try:
            return self._runtime.session_store.create_session(
                context_id=self._runtime.context_id,
                name=default_name,
                provider_locked=self._provider_id,
                is_ephemeral=False,
            )
        except Exception:
            sessions = self._runtime.session_store.list_sessions(
                context_id=self._runtime.context_id,
                include_ephemeral=True,
            )
            for session in sessions:
                if session.name == default_name:
                    return session
            return self._runtime.session_store.create_session(
                context_id=self._runtime.context_id,
                name=None,
                provider_locked=self._provider_id,
                is_ephemeral=False,
            )

    def _approval_resolver(self):
        if self._yes:
            return lambda _call, _risk: ApprovalAction(allow=True, reason="service_yes")

        def resolver(call: ToolCall, risk_tier: str) -> ApprovalAction:
            return ApprovalAction(
                allow=False,
                reason="approval_required_in_service:{0}:{1}".format(call.tool_name, risk_tier),
            )

        return resolver

    def _emit(
        self,
        kind: str,
        text: str,
        *,
        contact_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> None:
        log_diagnostic = getattr(self._runtime, "log_diagnostic", None)
        if callable(log_diagnostic):
            try:
                event_meta = dict(meta or {})
                event_type = event_meta.get("event_type")
                level = "error" if kind == "error" else "info"
                conversation_id = None
                if self._binding.session_id:
                    conversation_id = "session.{0}".format(self._binding.session_id)
                log_diagnostic(
                    level=level,
                    component="service",
                    kind="service_event",
                    event_type=str(event_type or kind),
                    conversation_id=conversation_id,
                    message=text,
                    data={
                        "channel": self._channel.channel_name,
                        "kind": kind,
                        "contact_id": contact_id,
                        "chat_id": chat_id,
                        "meta": event_meta,
                    },
                )
            except Exception:
                pass

        if self._event_sink is None:
            return
        self._event_sink(
            ServiceEvent(
                kind=kind,
                text=text,
                channel=self._channel.channel_name,
                contact_id=contact_id,
                chat_id=chat_id,
                meta=dict(meta or {}),
            )
        )

    def _channel_health_snapshot(self) -> ChannelHealthSnapshot:
        health_fn = getattr(self._channel, "health_snapshot", None)
        if callable(health_fn):
            try:
                health = health_fn()
                if isinstance(health, ChannelHealthSnapshot):
                    return health
            except Exception:
                pass
        return ChannelHealthSnapshot(listener_state="unknown", listener_alive=False)

    def _apply_channel_scope_locked(self) -> None:
        scope_fn = getattr(self._channel, "set_chat_scope", None)
        if not callable(scope_fn):
            return
        try:
            # Reliability-first: keep watch unscoped and enforce authorization via
            # normalized contact matching only. Some environments may emit inbound
            # messages on different chat ids for the same sender, which causes
            # strict chat-scoped watch to miss traffic and skip ACK/reply.
            scope_fn(None)
        except Exception as exc:
            self._emit(
                "error",
                render_notice(
                    "error",
                    "更新渠道监听范围失败：{0}".format(exc),
                    "Failed to update channel scope: {0}".format(exc),
                ),
            )

    def _ensure_ingest_mode_locked(self) -> None:
        self._channel.stop_listener()
        self._start_pairing_poller_locked()

    def _start_pairing_poller_locked(self) -> None:
        if self._pair_poll_thread and self._pair_poll_thread.is_alive():
            return
        self._pair_poll_stop.clear()
        self._pair_poll_thread = threading.Thread(
            target=self._pairing_poll_loop,
            daemon=True,
            name="perlica-service-pair-poll",
        )
        self._pair_poll_thread.start()

    def _stop_pairing_poller_locked(self) -> None:
        self._pair_poll_stop.set()
        if self._pair_poll_thread and self._pair_poll_thread.is_alive():
            self._pair_poll_thread.join(timeout=1.5)
        self._pair_poll_thread = None

    def _pairing_poll_loop(self) -> None:
        while self._running and not self._pair_poll_stop.is_set():
            try:
                with self._run_lock:
                    paired = bool(self._binding.paired)
                    pairing_code = self._pair_code
                    bound_contact = self._binding.contact_id
                    bound_chat_id = self._binding.chat_id
                    paired_at_ms = self._binding.paired_at_ms
                    poll_since_ts_ms = self._poll_since_ts_ms

                if not paired:
                    if not pairing_code:
                        self._pair_poll_stop.wait(0.5)
                        continue

                    poll_fn = getattr(self._channel, "poll_for_pairing_code", None)
                    if not callable(poll_fn):
                        self._pair_poll_stop.wait(0.5)
                        continue

                    matched = poll_fn(pairing_code, max_chats=8)
                    if matched is not None:
                        self._on_channel_message(matched)
                    self._pair_poll_stop.wait(0.5)
                    continue

                poll_recent_fn = getattr(self._channel, "poll_recent_messages", None)
                if not callable(poll_recent_fn) or not bound_contact:
                    self._pair_poll_stop.wait(0.5)
                    continue

                polled_messages = poll_recent_fn(
                    contact_id=bound_contact,
                    chat_id=bound_chat_id,
                    since_ts_ms=max(int(paired_at_ms or 0), int(poll_since_ts_ms or 0)),
                    max_chats=8,
                    limit_per_chat=8,
                )
                latest_polled_ts_ms = int(poll_since_ts_ms or 0)
                for message in polled_messages or []:
                    message_ts_ms = int(message.ts_ms or 0)
                    if message_ts_ms > latest_polled_ts_ms:
                        latest_polled_ts_ms = message_ts_ms
                if latest_polled_ts_ms > int(poll_since_ts_ms or 0):
                    with self._run_lock:
                        if latest_polled_ts_ms + 1 > int(self._poll_since_ts_ms or 0):
                            self._poll_since_ts_ms = latest_polled_ts_ms + 1
                if polled_messages:
                    self._emit(
                        "telemetry",
                        "poll 捕获到 {0} 条消息。".format(len(polled_messages)),
                        meta={
                            "event_type": "inbound.polled",
                            "direction": "inbound",
                            "count": len(polled_messages),
                        },
                    )
                for message in polled_messages or []:
                    self._on_channel_message(message)
            except Exception as exc:
                self._emit(
                    "error",
                    render_notice(
                        "error",
                        "轮询配对失败：{0}".format(exc),
                        "Pairing poll failed: {0}".format(exc),
                    ),
                )
            self._pair_poll_stop.wait(0.5)

    def _is_duplicate_inbound_event_id(self, event_id: str) -> bool:
        normalized = str(event_id or "").strip()
        if not normalized:
            return False
        return normalized in self._recent_inbound_event_id_set

    def _mark_inbound_event_id(self, event_id: str) -> None:
        normalized = str(event_id or "").strip()
        if not normalized:
            return
        if normalized in self._recent_inbound_event_id_set:
            return
        if len(self._recent_inbound_event_ids) >= self._recent_inbound_event_ids.maxlen:
            oldest = self._recent_inbound_event_ids.popleft()
            self._recent_inbound_event_id_set.discard(oldest)
        self._recent_inbound_event_ids.append(normalized)
        self._recent_inbound_event_id_set.add(normalized)

    @staticmethod
    def _inbound_dedupe_key(inbound: ChannelInboundMessage) -> str:
        event_id = str(inbound.event_id or "").strip()
        if not event_id:
            return ""
        from_me_tag = "me" if bool(inbound.is_from_me) else "remote"
        return "{0}:{1}".format(from_me_tag, event_id)


def _format_age(ts_ms: Optional[int]) -> str:
    if ts_ms is None:
        return "never"
    delta = now_ms() - int(ts_ms)
    if delta < 0:
        delta = 0
    if delta < 1000:
        return "now"
    seconds = int(delta / 1000)
    if seconds < 60:
        return "{0}s".format(seconds)
    minutes = int(seconds / 60)
    if minutes < 60:
        return "{0}m".format(minutes)
    hours = int(minutes / 60)
    return "{0}h".format(hours)
