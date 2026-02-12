"""Runtime loading indicator for CLI executions."""

from __future__ import annotations

import itertools
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional, TextIO


@dataclass
class LoadingState:
    stage: str = "init"
    context_id: str = ""
    session_id: str = ""
    provider_id: str = ""
    detail: str = ""


class LoadingReporter:
    """Displays run progress in TTY and a static status in non-TTY."""

    def __init__(self, stream: Optional[TextIO] = None, enabled: Optional[bool] = None) -> None:
        self._stream = stream or sys.stderr
        self._enabled = bool(enabled) if enabled is not None else bool(self._stream.isatty())
        term = str(os.getenv("TERM") or "")
        self._supports_ansi = self._enabled and term.lower() not in {"", "dumb"}
        self._state = LoadingState()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, context_id: str, session_id: str, provider_id: str) -> None:
        with self._lock:
            self._state.context_id = context_id
            self._state.session_id = session_id
            self._state.provider_id = provider_id
            self._state.stage = "resolve-session"
            self._state.detail = ""

        if not self._enabled:
            self._stream.write(
                "正在运行 (running)... context={0} session={1} provider={2}\n".format(
                    context_id,
                    session_id,
                    provider_id,
                )
            )
            self._stream.flush()
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="perlica-loading", daemon=True)
        self._thread.start()

    def update(
        self,
        stage: str,
        context_id: Optional[str] = None,
        session_id: Optional[str] = None,
        provider_id: Optional[str] = None,
        detail: str = "",
    ) -> None:
        with self._lock:
            self._state.stage = stage
            if context_id is not None:
                self._state.context_id = context_id
            if session_id is not None:
                self._state.session_id = session_id
            if provider_id is not None:
                self._state.provider_id = provider_id
            self._state.detail = detail

    def stop(self) -> None:
        if not self._enabled:
            return

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

        self._clear_line()
        self._stream.flush()

    def _loop(self) -> None:
        spinner = itertools.cycle(["|", "/", "-", "\\"])
        while not self._stop_event.is_set():
            symbol = next(spinner)
            with self._lock:
                line = self._render(symbol)
            self._write_frame(line)
            self._stream.flush()
            time.sleep(0.1)

    def _render(self, symbol: str) -> str:
        stage = self._stage_label(self._state.stage)
        context_id = self._shrink_value(self._state.context_id, 12)
        session_id = self._shrink_value(self._state.session_id, 12)
        provider_id = self._shrink_value(self._state.provider_id, 10)
        detail = self._shrink_value(self._state.detail, 16) if self._state.detail else ""
        line = "[{0}] {1} ctx={2} sess={3} model={4}".format(
            symbol,
            stage,
            context_id,
            session_id,
            provider_id,
        )
        if detail:
            line += " | step={0}".format(detail)
        return self._fit_line(line)

    @staticmethod
    def _stage_label(stage: str) -> str:
        if stage == "resolve-session":
            return "resolve"
        if stage == "load-context":
            return "context"
        if stage == "tool-dispatch":
            return "tool"
        if stage == "finalize":
            return "finalize"
        if stage.startswith("llm-call-"):
            suffix = stage.split("llm-call-", 1)[1]
            return "llm-{0}".format(suffix)
        return stage

    def _fit_line(self, line: str) -> str:
        columns = max(40, int(shutil.get_terminal_size(fallback=(120, 20)).columns))
        max_len = max(20, columns - 1)
        if len(line) <= max_len:
            return line
        return line[: max_len - 3] + "..."

    @staticmethod
    def _shrink_value(value: str, max_len: int) -> str:
        if len(value) <= max_len:
            return value
        keep = max(3, (max_len - 2) // 2)
        return value[:keep] + ".." + value[-keep:]

    def _clear_line(self) -> None:
        if self._supports_ansi:
            self._stream.write("\r\033[2K")
            return
        self._stream.write("\r" + (" " * 160) + "\r")

    def _write_frame(self, line: str) -> None:
        self._clear_line()
        self._stream.write(line)
