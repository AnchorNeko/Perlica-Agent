"""Single-active-task coordinator shared by TUI/service/runtime."""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

from perlica.task.types import TaskSnapshot, TaskState

TaskEventSink = Callable[[str, Dict[str, Any]], None]


class TaskCoordinator:
    """Tracks one active run per runtime and enforces serial execution."""

    def __init__(self, event_sink: Optional[TaskEventSink] = None) -> None:
        self._event_sink = event_sink
        self._lock = threading.RLock()
        self._state: TaskState = TaskState.IDLE
        self._run_id = ""
        self._conversation_id = ""
        self._session_id = ""
        self._interaction_id = ""
        self._metadata: Dict[str, Any] = {}

    def snapshot(self) -> TaskSnapshot:
        with self._lock:
            return TaskSnapshot(
                state=self._state,
                run_id=self._run_id,
                conversation_id=self._conversation_id,
                session_id=self._session_id,
                interaction_id=self._interaction_id,
                metadata=dict(self._metadata),
            )

    def start_task(
        self,
        *,
        run_id: str,
        conversation_id: str,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        with self._lock:
            if self._state in {TaskState.RUNNING, TaskState.AWAITING_INTERACTION}:
                return False
            self._state = TaskState.RUNNING
            self._run_id = str(run_id or "")
            self._conversation_id = str(conversation_id or "")
            self._session_id = str(session_id or "")
            self._interaction_id = ""
            self._metadata = dict(metadata or {})
        self._emit(
            "task.started",
            {
                "run_id": self._run_id,
                "conversation_id": self._conversation_id,
                "session_id": self._session_id,
                "state": self._state.value,
            },
        )
        self._emit_state_changed(reason="start")
        return True

    def mark_waiting_interaction(
        self,
        *,
        interaction_id: str,
        run_id: Optional[str] = None,
    ) -> None:
        with self._lock:
            if self._state == TaskState.IDLE:
                return
            if run_id and self._run_id and str(run_id) != self._run_id:
                return
            self._state = TaskState.AWAITING_INTERACTION
            self._interaction_id = str(interaction_id or "")
        self._emit_state_changed(reason="interaction_requested")

    def submit_interaction_answer(self, *, interaction_id: str) -> bool:
        with self._lock:
            if self._state != TaskState.AWAITING_INTERACTION:
                return False
            if self._interaction_id and str(interaction_id or "") != self._interaction_id:
                return False
            self._state = TaskState.RUNNING
            self._interaction_id = ""
        self._emit_state_changed(reason="interaction_answered")
        return True

    def finish_task(self, *, run_id: Optional[str], failed: bool = False) -> None:
        with self._lock:
            if run_id and self._run_id and str(run_id) != self._run_id:
                return
            self._state = TaskState.FAILED if failed else TaskState.COMPLETED
        self._emit_state_changed(reason="finish")
        with self._lock:
            self._state = TaskState.IDLE
            self._run_id = ""
            self._conversation_id = ""
            self._session_id = ""
            self._interaction_id = ""
            self._metadata = {}
        self._emit_state_changed(reason="idle")

    def reject_new_command_if_busy(self) -> Optional[str]:
        snapshot = self.snapshot()
        if not snapshot.has_active_task:
            return None
        if snapshot.waiting_interaction:
            return "当前任务正在等待确认，请先回答待确认问题（/pending 或 /choose）。"
        return "上一条指令仍在执行中，请等待完成后再发送新指令。"

    def _emit_state_changed(self, *, reason: str) -> None:
        self._emit(
            "task.state.changed",
            {
                "state": self._state.value,
                "run_id": self._run_id,
                "conversation_id": self._conversation_id,
                "session_id": self._session_id,
                "interaction_id": self._interaction_id,
                "reason": reason,
            },
        )

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(str(event_type), dict(payload))
        except Exception:
            return

