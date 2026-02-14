"""Textual app for Perlica foreground service bridge mode."""

from __future__ import annotations

import threading
from typing import Optional

from rich.panel import Panel
from rich.text import Text

from perlica.service.presentation import map_service_event_to_view
from perlica.service.types import ServiceEvent
from perlica.tui.service_controller import ServiceController
from perlica.tui.widgets import ChatInput
from perlica.ui.render import render_notice

try:  # pragma: no cover - runtime dependency
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, RichLog, Static

    _HAS_TEXTUAL = True
    _TEXTUAL_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - when textual missing
    _HAS_TEXTUAL = False
    _TEXTUAL_IMPORT_ERROR = exc


def textual_available() -> bool:
    return _HAS_TEXTUAL


def textual_import_error() -> Optional[Exception]:
    return _TEXTUAL_IMPORT_ERROR


_DEFAULT_INPUT_HINT = (
    "发送: Enter / Ctrl+S  |  换行: Ctrl+J / Ctrl+N / Shift+Enter "
    "(Send/Newline shortcuts)"
)
_WAITING_CHANNEL_HINT = (
    "未激活渠道：先执行 /service channel list，再执行 /service channel use imessage "
    "(Activate channel via slash commands)"
)


if _HAS_TEXTUAL:

    class PerlicaServiceApp(App[None]):
        """Foreground bridge UI for channel service mode."""

        CSS = """
        Screen {
            layout: vertical;
            background: #0f1115;
            color: #f1f1f1;
        }

        #status {
            height: auto;
            padding: 0 1;
            background: #1b1f2a;
            color: #dddddd;
        }

        #service-log {
            height: 1fr;
            border: round #2e3851;
            padding: 0 1;
        }

        #service-input {
            border: round #355081;
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
        """

        BINDINGS = [
            Binding("ctrl+d", "request_exit", "退出"),
            Binding("ctrl+l", "clear_log", "清屏"),
        ]

        def __init__(self, controller: ServiceController) -> None:
            super().__init__()
            self._controller = controller
            self._busy = False
            self._phase = "等待渠道激活 (Channel inactive)"
            self._channel_options = controller.list_channel_options()
            self._last_pending_marker = ""

        def compose(self) -> ComposeResult:
            yield Vertical(
                Static("", id="status"),
                RichLog(id="service-log", highlight=False, markup=True, wrap=True),
                Horizontal(
                    ChatInput(id="service-input"),
                    id="input-row",
                ),
                Static(_WAITING_CHANNEL_HINT, id="input-hint"),
                Footer(),
            )

        def on_mount(self) -> None:
            self.title = "Perlica Service"
            self.sub_title = "Channel Bridge"
            self._controller.set_event_sink(self._on_service_event)
            self._controller.start()
            self._append_channel_summary()
            self._refresh_status()
            self.set_interval(1.0, self._tick_status)
            self.set_interval(0.2, self._tick_pending_interaction)
            self.query_one("#service-input").focus()
            self._refresh_status()

        def action_submit(self) -> None:
            text = self._get_input_text().strip()
            if not text:
                return
            if self._busy and not self._controller.has_pending_interaction() and not text.startswith("/"):
                self._controller.emit_task_command_rejected(source="local", text=text)
                self._append_system(self._controller.busy_reject_message())
                return
            self._set_input_text("")
            self._append_local(text)
            self._busy = True
            self._phase = "处理中 (Working)"
            self._refresh_status()

            worker = threading.Thread(
                target=self._run_local_submit,
                args=(text,),
                daemon=True,
                name="perlica-service-local-submit",
            )
            worker.start()

        def _run_local_submit(self, text: str) -> None:
            try:
                output = self._controller.submit_input(text)
            except Exception as exc:  # pragma: no cover
                output = render_notice(
                    "error",
                    "本地执行失败：{0}".format(exc),
                    "Local execution failed: {0}".format(exc),
                )
            self.call_from_thread(self._finish_local_submit, text, output)

        def _finish_local_submit(self, text: str, output: str) -> None:
            self._busy = False
            if output:
                if text.startswith("/"):
                    self._append_system(output)
                elif self._controller.has_active_channel():
                    self._append_assistant(output)
                else:
                    self._append_system(output)
            self._phase = (
                "监听中 (Listening)"
                if self._controller.has_active_channel()
                else "等待渠道激活 (Channel inactive)"
            )
            self._refresh_status()

        def on_text_area_changed(self, message: TextArea.Changed) -> None:
            self._refresh_input_hint(str(message.control.text))

        def on_chat_input_submitted(self, message: ChatInput.Submitted) -> None:
            message.stop()
            self.action_submit()

        def action_clear_log(self) -> None:
            self.query_one("#service-log", RichLog).clear()
            self._append_system("已清空服务日志。")

        def action_request_exit(self) -> None:
            self.exit()

        def _on_service_event(self, event: ServiceEvent) -> None:
            if threading.current_thread() is threading.main_thread():
                self._render_service_event(event)
                return
            self.call_from_thread(self._render_service_event, event)

        def _render_service_event(self, event: ServiceEvent) -> None:
            view = map_service_event_to_view(event)
            if view is None:
                return
            self.query_one("#service-log", RichLog).write(
                Panel(Text(view.text), title=view.title, border_style=view.border_style),
                scroll_end=True,
            )
            if not self._busy:
                self._phase = view.phase
            self._refresh_status()

        def _tick_status(self) -> None:
            if not self._busy and self._controller.has_active_channel():
                snapshot = self._controller.status_snapshot()
                if snapshot.health.listener_state == "error":
                    self._phase = "监听异常 (Listener issue)"
                else:
                    self._phase = "监听中 (Listening)"
            elif not self._busy:
                self._phase = "等待渠道激活 (Channel inactive)"
            self._refresh_status()

        def _refresh_status(self) -> None:
            status_text = self._controller.status_text()
            self.query_one("#status", Static).update(
                "[b]service[/b]  [dim]|[/dim]  {0}  [dim]|[/dim]  [dim]{1}[/dim]".format(
                    status_text,
                    self._phase,
                )
            )

        def _get_input_text(self) -> str:
            widget = self.query_one("#service-input")
            return str(getattr(widget, "text", ""))

        def _set_input_text(self, value: str) -> None:
            widget = self.query_one("#service-input")
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
                built = self._controller.build_slash_hint_text(text).strip()
                if built:
                    hint = built
            elif self._controller.has_pending_interaction():
                hint = "检测到待确认交互：直接输入编号/文本回答，或使用 /pending /choose。"
            elif not self._controller.has_active_channel():
                hint = _WAITING_CHANNEL_HINT
            self.query_one("#input-hint", Static).update(hint)

        def _tick_pending_interaction(self) -> None:
            if not self._controller.has_pending_interaction():
                self._last_pending_marker = ""
                return

            pending_text = self._controller.pending_interaction_text()
            marker = ""
            for line in pending_text.splitlines():
                if line.startswith("交互ID:"):
                    marker = line.split(":", 1)[1].strip()
                    break
            if not marker:
                marker = pending_text
            if marker == self._last_pending_marker:
                return
            self._last_pending_marker = marker
            self._append_system(pending_text)
            if not self._busy:
                self._phase = "等待确认 (Awaiting confirmation)"
                self._refresh_status()

        def _append_channel_summary(self) -> None:
            if not self._channel_options:
                self._append_error("未发现可用渠道。")
                return

            lines = [
                "当前未激活渠道，请先执行命令：",
                "- /service channel list",
                "- /service channel use imessage",
                "",
                "可选渠道：",
            ]
            for item in self._channel_options:
                status = "可用" if item.available else "不可用"
                suffix = ""
                if item.reason:
                    suffix = " - {0}".format(item.reason)
                lines.append(
                    "- {0} ({1}) {2}{3}".format(
                        item.display_name,
                        item.channel_id,
                        status,
                        suffix,
                    )
                )
            self._append_system("\n".join(lines))

        def _append_local(self, text: str) -> None:
            self.query_one("#service-log", RichLog).write(
                Panel(Text(text), title="本地输入 (Local)", border_style="#4ba3ff"),
                scroll_end=True,
            )

        def _append_assistant(self, text: str) -> None:
            self.query_one("#service-log", RichLog).write(
                Panel(Text(text), title="助手回复 (Assistant)", border_style="#62d26f"),
                scroll_end=True,
            )

        def _append_system(self, text: str) -> None:
            self.query_one("#service-log", RichLog).write(
                Panel(Text(text), title="系统 (System)", border_style="#d9b600"),
                scroll_end=True,
            )

        def _append_error(self, text: str) -> None:
            self.query_one("#service-log", RichLog).write(
                Panel(Text(text), title="错误 (Error)", border_style="#f25f5c"),
                scroll_end=True,
            )

else:

    class PerlicaServiceApp:  # pragma: no cover
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError(
                "Textual 未安装或初始化失败：{0}".format(_TEXTUAL_IMPORT_ERROR)
            )
