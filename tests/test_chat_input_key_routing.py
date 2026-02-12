from __future__ import annotations

import asyncio

import pytest

from perlica.tui import app as tui_app
from perlica.tui.types import ChatStatus, SlashOutcome
from perlica.tui.widgets import classify_chat_input_key


class _FakeController:
    def __init__(self) -> None:
        self.phase = "就绪 (Ready)"

    def status(self) -> ChatStatus:
        return ChatStatus(
            model="claude",
            session_title="临时会话",
            context_id="default",
            phase=self.phase,
        )

    def set_phase(self, phase: str) -> None:
        self.phase = phase

    def run_slash_command(self, raw_line: str) -> SlashOutcome:
        return SlashOutcome(handled=True, output_text="handled: {0}".format(raw_line))

    def build_slash_hint_text(self, raw_input: str) -> str:
        del raw_input
        return ""

    def run_user_message(self, text: str, progress_callback=None):
        del text
        del progress_callback
        raise RuntimeError("unused in key routing tests")

    def should_confirm_exit(self) -> bool:
        return False

    def save_current_session(self, name=None) -> str:
        del name
        return "saved"

    def discard_current_session(self) -> str:
        return "discarded"


def test_chat_input_key_classification_routes():
    assert classify_chat_input_key("enter") == "submit"
    assert classify_chat_input_key("ctrl+s") == "submit"
    assert classify_chat_input_key("shift+enter") == "newline"
    assert classify_chat_input_key("ctrl+j") == "newline"
    assert classify_chat_input_key("x") == ""


def test_chat_input_unhandled_key_falls_back_to_textarea_behavior():
    if not tui_app.textual_available():
        pytest.skip("textual not available")

    async def _run() -> None:
        app = tui_app.PerlicaChatApp(controller=_FakeController())
        async with app.run_test() as pilot:
            await pilot.pause()
            widget = app.query_one("#chat-input")

            await pilot.press("a")
            await pilot.pause()

            assert "a" in widget.text

    asyncio.run(_run())
