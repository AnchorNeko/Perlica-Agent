"""Textual widgets for Perlica chat UI."""

from __future__ import annotations

from typing import Tuple

try:  # pragma: no cover - runtime dependency
    from textual.containers import Horizontal, Vertical
    from textual import events
    from textual.message import Message
    from textual.screen import ModalScreen
    from textual.widgets import Button, Input, Static, TextArea

    _HAS_TEXTUAL = True
except Exception:  # pragma: no cover - imported lazily
    _HAS_TEXTUAL = False


def classify_chat_input_key(key: str) -> str:
    """Map chat input keystrokes to submit/newline/fallback actions."""

    normalized = (key or "").lower()
    if normalized in {"enter", "return", "ctrl+m", "ctrl+s"}:
        return "submit"
    if normalized in {"shift+enter", "ctrl+j", "ctrl+n", "alt+enter", "ctrl+enter"}:
        return "newline"
    return ""


if _HAS_TEXTUAL:

    class StatusBar(Static):
        """Top status bar: model | session | context | phase."""

        def set_status(self, model: str, session: str, context_id: str, phase: str) -> None:
            content = (
                "[b]{0}[/b]  [dim]|[/dim]  {1}  [dim]|[/dim]  {2}  "
                "[dim]|[/dim]  [dim]{3}[/dim]"
            ).format(model, session, context_id, phase)
            self.update(content)


    class ChatInput(TextArea):
        """Text input area with explicit send/newline key routing."""

        BINDINGS = []

        class Submitted(Message):
            """Posted when user requests sending the current input."""

        async def _on_key(self, event: events.Key) -> None:
            action = classify_chat_input_key(event.key)

            if action == "submit":
                event.prevent_default()
                event.stop()
                self.post_message(self.Submitted())
                return

            if action == "newline":
                event.prevent_default()
                event.stop()
                insert = getattr(self, "insert", None)
                if callable(insert):
                    insert("\n")
                else:
                    self.load_text(str(getattr(self, "text", "")) + "\n")
                return

            await super()._on_key(event)


    class ExitConfirmScreen(ModalScreen[Tuple[str, str]]):
        """Prompt when leaving an unsaved temporary session."""

        BINDINGS = [("escape", "cancel", "取消 (Cancel)")]

        def compose(self):
            yield Vertical(
                Static("检测到未保存的临时会话 (Unsaved temporary session)."),
                Static("请选择：保存 / 放弃 / 取消 (Save / Discard / Cancel)"),
                Input(placeholder="保存名称（可选） (Optional session name)", id="save-name"),
                Horizontal(
                    Button("保存 (Save)", variant="success", id="save"),
                    Button("放弃 (Discard)", variant="warning", id="discard"),
                    Button("取消 (Cancel)", variant="default", id="cancel"),
                    id="exit-buttons",
                ),
                id="exit-confirm-body",
            )

        def action_cancel(self) -> None:
            self.dismiss(("cancel", ""))

        def on_button_pressed(self, event: Button.Pressed) -> None:
            button_id = event.button.id or ""
            name_value = self.query_one("#save-name", Input).value.strip()
            if button_id == "save":
                self.dismiss(("save", name_value))
                return
            if button_id == "discard":
                self.dismiss(("discard", ""))
                return
            self.dismiss(("cancel", ""))

else:

    class StatusBar:  # pragma: no cover - fallback class when Textual is missing
        pass


    class ChatInput:  # pragma: no cover - fallback class when Textual is missing
        pass


    class ExitConfirmScreen:  # pragma: no cover - fallback class when Textual is missing
        pass
