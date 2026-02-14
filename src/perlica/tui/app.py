"""Textual application for Perlica interactive chat."""

from __future__ import annotations

import threading
import time
from typing import Optional, Tuple

from rich.panel import Panel
from rich.text import Text

from perlica.providers.base import ProviderError
from perlica.tui.controller import ChatController, format_provider_error
from perlica.tui.widgets import ChatInput, ExitConfirmScreen, StatusBar
from perlica.ui.render import render_notice

try:  # pragma: no cover - runtime dependency
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, RichLog, Static, TextArea

    _HAS_TEXTUAL = True
    _TEXTUAL_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - when textual is missing
    _HAS_TEXTUAL = False
    _TEXTUAL_IMPORT_ERROR = exc


def textual_available() -> bool:
    return _HAS_TEXTUAL


def textual_import_error() -> Optional[Exception]:
    return _TEXTUAL_IMPORT_ERROR


def _chunk_text(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return [""]
    chunks: list[str] = []
    current = []
    for char in stripped:
        current.append(char)
        if char in {"。", "！", "？", ".", "!", "?", "\n"}:
            chunks.append("".join(current).strip())
            current = []
    tail = "".join(current).strip()
    if tail:
        chunks.append(tail)
    return chunks or [stripped]


def _stage_label(stage: str, detail: str) -> str:
    if stage == "resolve-session":
        base = "解析会话 (Resolve session)"
    elif stage == "load-context":
        base = "加载上下文 (Load context)"
    elif stage.startswith("llm-call-"):
        base = "模型调用 (LLM call)"
    elif stage == "tool-dispatch":
        base = "工具执行 (Tool dispatch)"
    elif stage == "finalize":
        base = "整理输出 (Finalize)"
    else:
        base = stage
    if detail:
        return "{0}: {1}".format(base, detail)
    return base


_DEFAULT_INPUT_HINT = (
    "发送: Enter / Ctrl+S  |  换行: Ctrl+J / Ctrl+N / Shift+Enter "
    "(Send/Newline shortcuts)"
)


if _HAS_TEXTUAL:

    class PerlicaChatApp(App[None]):
        """Perlica chat TUI with Claude-style interaction rhythm."""

        CSS = """
        Screen {
            layout: vertical;
            background: #111111;
            color: #f1f1f1;
        }

        #status {
            height: 1;
            padding: 0 1;
            background: #1d1d1d;
            color: #dddddd;
        }

        #chat-log {
            height: 1fr;
            border: round #2f2f2f;
            padding: 0 1;
            scrollbar-size: 1 1;
        }

        #chat-input {
            border: round #3a3a3a;
            height: 5;
            margin: 1 0 0 0;
        }

        #input-row {
            height: auto;
            margin: 1 0 0 0;
        }

        #input-hint {
            height: auto;
            color: #8d8d8d;
            padding: 0 1;
        }

        #exit-confirm-body {
            width: 72;
            max-width: 90%;
            height: auto;
            border: round #4a4a4a;
            background: #171717;
            padding: 1 2;
        }

        #exit-buttons {
            margin-top: 1;
            height: auto;
            align-horizontal: right;
        }
        """

        BINDINGS = [
            Binding("ctrl+c", "cancel_generation", "取消生成"),
            Binding("ctrl+d", "request_exit", "退出"),
            Binding("ctrl+l", "clear_chat", "清屏"),
        ]

        def __init__(self, controller: ChatController) -> None:
            super().__init__()
            self._controller = controller
            self._generation_active = False
            self._cancel_requested = False
            self._last_pending_interaction_id = ""

        def compose(self) -> ComposeResult:
            yield Vertical(
                StatusBar(id="status"),
                RichLog(id="chat-log", highlight=False, markup=True, wrap=True),
                Horizontal(
                    ChatInput(id="chat-input"),
                    id="input-row",
                ),
                Static(_DEFAULT_INPUT_HINT, id="input-hint"),
                Footer(),
            )

        def on_mount(self) -> None:
            self.title = "Perlica"
            self.sub_title = "Claude 风格交互 (Claude-like chat)"
            self._refresh_status()
            self._append_system(
                "Perlica 会话已启动。\n"
                "输入自然语言直接对话；输入 /help 查看命令。\n"
                "发送: Enter / Ctrl+S；换行: Ctrl+J / Ctrl+N / Shift+Enter。"
            )
            self.set_interval(0.2, self._tick_interaction)
            self.query_one("#chat-input", TextArea).focus()

        def action_submit(self) -> None:
            text = self._get_input_text().strip()
            if not text:
                return
            pending = self._has_pending_interaction()
            if self._generation_active and not pending and not text.startswith("/"):
                self._controller.emit_task_command_rejected(source="local", text=text)
                self._append_system(self._controller.busy_reject_message())
                return
            self._set_input_text("")
            self._submit_user_text(text)

        def action_insert_newline(self) -> None:
            if self._generation_active:
                return
            widget = self.query_one("#chat-input", TextArea)
            insert = getattr(widget, "insert", None)
            if callable(insert):
                insert("\n")
                return
            self._set_input_text(self._get_input_text() + "\n")

        def on_text_area_changed(self, message: TextArea.Changed) -> None:
            self._refresh_input_hint(str(message.control.text))

        def on_chat_input_submitted(self, message: ChatInput.Submitted) -> None:
            message.stop()
            self.action_submit()

        def action_clear_chat(self) -> None:
            self.query_one("#chat-log", RichLog).clear()
            self._append_system("已清空会话区域。")

        def action_cancel_generation(self) -> None:
            if not self._generation_active:
                self._append_system("当前没有进行中的生成任务。")
                return
            self._cancel_requested = True
            self._controller.set_phase("取消中 (Canceling)")
            self._refresh_status()
            self._append_system("已请求取消当前生成，将在本轮完成后停止展示结果。")

        def action_request_exit(self) -> None:
            if self._generation_active:
                self._append_system("正在生成中，请稍后再退出。")
                return

            if self._controller.should_confirm_exit():
                self.push_screen(ExitConfirmScreen(), self._on_exit_confirm)
                return
            self.exit()

        def _on_exit_confirm(self, result: Tuple[str, str]) -> None:
            action, name = result
            if action == "save":
                message = self._controller.save_current_session(name=name or None)
                self._append_system(message)
                self.exit()
                return
            if action == "discard":
                message = self._controller.discard_current_session()
                self._append_system(message)
                self.exit()
                return
            self._append_system("已取消退出。")

        def _handle_slash(self, text: str) -> None:
            outcome = self._controller.run_slash_command(text)
            if outcome.exit_requested:
                self.action_request_exit()
                return
            if outcome.handled:
                if outcome.output_text:
                    self._append_system(outcome.output_text)
                self._refresh_status()
                return

            # Unknown /command: fallback to model input.
            fallback = outcome.fallback_text or text
            self._append_user(fallback)
            self._start_generation(fallback)

        def _start_generation(self, text: str) -> None:
            self._generation_active = True
            self._cancel_requested = False
            self._controller.set_phase("准备中 (Preparing)")
            self._refresh_status()
            worker = threading.Thread(
                target=self._run_generation_worker,
                args=(text,),
                daemon=True,
                name="perlica-tui-generate",
            )
            worker.start()

        def _submit_user_text(self, text: str) -> None:
            if text.startswith("/"):
                self._handle_slash(text)
                return

            if self._has_pending_interaction():
                self._append_user(text)
                feedback = self._submit_pending_answer(text, source="local")
                self._append_system(feedback)
                self._refresh_status()
                return

            self._append_user(text)
            self._start_generation(text)

        def _run_generation_worker(self, text: str) -> None:
            try:
                result = self._controller.run_user_message(
                    text=text,
                    progress_callback=self._on_progress,
                )
                if self._cancel_requested:
                    self.call_from_thread(
                        self._append_system,
                        "本轮结果已取消展示。你可以继续输入下一条消息。",
                    )
                    return

                for chunk in _chunk_text(result.assistant_text):
                    self.call_from_thread(self._append_stream_chunk, chunk)
                    time.sleep(0.03)

                self.call_from_thread(self._append_assistant, result.assistant_text)
                self.call_from_thread(self._append_meta, result)
            except ProviderError as exc:
                self.call_from_thread(self._append_error, format_provider_error(exc))
            except Exception as exc:  # pragma: no cover - defensive
                self.call_from_thread(
                    self._append_error,
                    render_notice(
                        "error",
                        "执行失败：{0}".format(exc),
                        "Execution failed: {0}".format(exc),
                    ),
                )
            finally:
                self.call_from_thread(self._finish_generation)

        def _on_progress(self, stage: str, payload: dict) -> None:
            detail = str(payload.get("detail") or "")
            self.call_from_thread(self._update_phase, _stage_label(stage, detail))

        def _update_phase(self, phase: str) -> None:
            self._controller.set_phase(phase)
            self._refresh_status()

        def _finish_generation(self) -> None:
            self._generation_active = False
            self._cancel_requested = False
            self._controller.set_phase("就绪 (Ready)")
            self._refresh_status()

        def _refresh_status(self) -> None:
            status = self._controller.status()
            self.query_one("#status", StatusBar).set_status(
                model=status.model,
                session=status.session_title,
                context_id=status.context_id,
                phase=status.phase,
            )

        def _get_input_text(self) -> str:
            widget = self.query_one("#chat-input")
            return str(getattr(widget, "text", ""))

        def _set_input_text(self, value: str) -> None:
            widget = self.query_one("#chat-input")
            load_text = getattr(widget, "load_text", None)
            if callable(load_text):
                load_text(value)
                self._refresh_input_hint(value)
                return
            if hasattr(widget, "text"):
                widget.text = value
            self._refresh_input_hint(value)

        def _refresh_input_hint(self, text: str) -> None:
            hint = _DEFAULT_INPUT_HINT
            if text.startswith("/"):
                hint_builder = getattr(self._controller, "build_slash_hint_text", None)
                if callable(hint_builder):
                    built = str(hint_builder(text)).strip()
                    if built:
                        hint = built
            elif self._has_pending_interaction():
                hint = "检测到待确认交互：直接输入编号/文本回答，或使用 /pending /choose。"
            self.query_one("#input-hint", Static).update(hint)

        def _append_stream_chunk(self, chunk: str) -> None:
            if not chunk:
                return
            self.query_one("#chat-log", RichLog).write(
                "[dim]··· {0}[/dim]".format(chunk),
                scroll_end=True,
            )

        def _append_user(self, text: str) -> None:
            self.query_one("#chat-log", RichLog).write(
                Panel(
                    Text(text),
                    title="你 (You)",
                    border_style="#4ba3ff",
                ),
                scroll_end=True,
            )

        def _append_assistant(self, text: str) -> None:
            self.query_one("#chat-log", RichLog).write(
                Panel(
                    Text(text),
                    title="助手 (Assistant)",
                    border_style="#62d26f",
                ),
                scroll_end=True,
            )

        def _append_system(self, text: str) -> None:
            self.query_one("#chat-log", RichLog).write(
                Panel(
                    Text(text),
                    title="系统 (System)",
                    border_style="#d9b600",
                ),
                scroll_end=True,
            )

        def _append_error(self, text: str) -> None:
            self.query_one("#chat-log", RichLog).write(
                Panel(
                    Text(text),
                    title="错误 (Error)",
                    border_style="#f25f5c",
                ),
                scroll_end=True,
            )

        def _append_meta(self, result) -> None:
            total = getattr(result, "total_usage", None)
            context_usage = dict(getattr(result, "context_usage", {}) or {})
            line = (
                "会话={0} | 上下文tokens≈{1} | input={2} cached={3} output={4}"
            ).format(
                getattr(result, "session_id", ""),
                int(context_usage.get("estimated_context_tokens") or 0),
                int(getattr(total, "input_tokens", 0)),
                int(getattr(total, "cached_input_tokens", 0)),
                int(getattr(total, "output_tokens", 0)),
            )
            self.query_one("#chat-log", RichLog).write("[dim]{0}[/dim]".format(line), scroll_end=True)

        def _tick_interaction(self) -> None:
            snapshot_text = self._interaction_pending_text()
            has_pending = self._has_pending_interaction()
            if not has_pending:
                self._last_pending_interaction_id = ""
                return

            # Deduplicate pending card rendering by interaction id from snapshot text.
            marker = ""
            for line in snapshot_text.splitlines():
                if line.startswith("交互ID:"):
                    marker = line.split(":", 1)[1].strip()
                    break
            if not marker:
                marker = snapshot_text
            if marker == self._last_pending_interaction_id:
                return

            self._last_pending_interaction_id = marker
            self._controller.set_phase("等待确认 (Awaiting confirmation)")
            self._refresh_status()
            self._append_system(snapshot_text)

        def _has_pending_interaction(self) -> bool:
            checker = getattr(self._controller, "has_pending_interaction", None)
            if not callable(checker):
                return False
            try:
                return bool(checker())
            except Exception:
                return False

        def _interaction_pending_text(self) -> str:
            getter = getattr(self._controller, "interaction_pending_text", None)
            if not callable(getter):
                return ""
            try:
                return str(getter() or "")
            except Exception:
                return ""

        def _submit_pending_answer(self, raw_input: str, source: str) -> str:
            submitter = getattr(self._controller, "submit_interaction_answer", None)
            if not callable(submitter):
                return render_notice("error", "当前无待确认交互。", "No pending interaction.")
            try:
                return str(submitter(raw_input, source=source))
            except Exception as exc:
                return render_notice(
                    "error",
                    "提交交互回答失败：{0}".format(exc),
                    "Failed to submit interaction answer: {0}".format(exc),
                )

else:

    class PerlicaChatApp:  # pragma: no cover - instantiated only when textual import failed
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError(
                "Textual 未安装或初始化失败：{0}".format(_TEXTUAL_IMPORT_ERROR)
            )
