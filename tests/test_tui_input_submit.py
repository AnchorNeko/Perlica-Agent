from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from perlica.tui import app as tui_app
from perlica.tui.types import ChatStatus, SlashOutcome


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
        if raw_input.startswith("/session new --provider"):
            return "命令: /session new  ·  可选: claude"
        if raw_input.startswith("/sess"):
            return "命令: /session  ·  可选: list | new | use | current"
        if raw_input.startswith("/"):
            return "命令: /  ·  可选: /help | /session"
        return ""

    def run_user_message(self, text: str, progress_callback=None):
        return SimpleNamespace(
            assistant_text="ok",
            session_id="sess_x",
            total_usage=SimpleNamespace(input_tokens=1, cached_input_tokens=0, output_tokens=1),
            context_usage={"estimated_context_tokens": 1},
        )

    def has_pending_interaction(self) -> bool:
        return False

    def interaction_pending_text(self) -> str:
        return ""

    def submit_interaction_answer(self, raw_input: str, source: str) -> str:
        return "submitted:{0}:{1}".format(raw_input, source)

    def should_confirm_exit(self) -> bool:
        return False

    def save_current_session(self, name=None) -> str:
        return "saved"

    def discard_current_session(self) -> str:
        return "discarded"


def test_action_submit_sends_text():
    if not tui_app.textual_available():
        pytest.skip("textual not available")

    async def _run() -> None:
        controller = _FakeController()
        app = tui_app.PerlicaChatApp(controller=controller)
        captured = []

        app._submit_user_text = lambda text: captured.append(text)  # type: ignore[attr-defined]

        async with app.run_test() as pilot:
            await pilot.pause()
            widget = app.query_one("#chat-input")
            widget.load_text("你好")
            app.action_submit()
            await pilot.pause()

            assert widget.text == ""
            assert captured == ["你好"]

    asyncio.run(_run())


def test_shift_enter_path_keeps_newline_before_submit():
    if not tui_app.textual_available():
        pytest.skip("textual not available")

    async def _run() -> None:
        controller = _FakeController()
        app = tui_app.PerlicaChatApp(controller=controller)
        captured = []
        app._submit_user_text = lambda text: captured.append(text)  # type: ignore[attr-defined]

        async with app.run_test() as pilot:
            await pilot.pause()
            widget = app.query_one("#chat-input")
            widget.load_text("第一行")

            app.action_insert_newline()
            await pilot.pause()
            assert "\n" in widget.text
            assert captured == []

            widget.load_text("第一行\n第二行")
            app.action_submit()
            await pilot.pause()

            assert captured == ["第一行\n第二行"]

    asyncio.run(_run())


def test_slash_first_char_updates_command_hint():
    if not tui_app.textual_available():
        pytest.skip("textual not available")

    async def _run() -> None:
        controller = _FakeController()
        app = tui_app.PerlicaChatApp(controller=controller)

        async with app.run_test() as pilot:
            await pilot.pause()
            widget = app.query_one("#chat-input")
            hint = app.query_one("#input-hint")

            assert "发送" in str(getattr(hint, "renderable", ""))

            widget.load_text("/")
            await pilot.pause()
            assert "/session" in str(getattr(hint, "renderable", ""))

            widget.load_text("/sess")
            await pilot.pause()
            rendered = str(getattr(hint, "renderable", ""))
            assert "/session" in rendered
            assert "list" in rendered

            widget.load_text("/session new --provider ")
            await pilot.pause()
            rendered = str(getattr(hint, "renderable", ""))
            assert "claude" in rendered

            widget.load_text("你好")
            await pilot.pause()
            assert "换行" in str(getattr(hint, "renderable", ""))

    asyncio.run(_run())


def test_press_enter_submits_from_chat_input_widget():
    if not tui_app.textual_available():
        pytest.skip("textual not available")

    async def _run() -> None:
        controller = _FakeController()
        app = tui_app.PerlicaChatApp(controller=controller)
        captured = []
        app._submit_user_text = lambda text: captured.append(text)  # type: ignore[attr-defined]

        async with app.run_test() as pilot:
            await pilot.pause()
            widget = app.query_one("#chat-input")
            widget.load_text("回车发送")
            await pilot.press("enter")
            await pilot.pause()

            assert captured == ["回车发送"]
            assert widget.text == ""

    asyncio.run(_run())


def test_press_shift_enter_inserts_newline_without_submit():
    if not tui_app.textual_available():
        pytest.skip("textual not available")

    async def _run() -> None:
        controller = _FakeController()
        app = tui_app.PerlicaChatApp(controller=controller)
        captured = []
        app._submit_user_text = lambda text: captured.append(text)  # type: ignore[attr-defined]

        async with app.run_test() as pilot:
            await pilot.pause()
            widget = app.query_one("#chat-input")
            widget.load_text("第一行")
            await pilot.press("shift+enter")
            await pilot.pause()

            assert "\n" in widget.text
            assert captured == []

    asyncio.run(_run())


def test_press_ctrl_j_inserts_newline_without_submit():
    if not tui_app.textual_available():
        pytest.skip("textual not available")

    async def _run() -> None:
        controller = _FakeController()
        app = tui_app.PerlicaChatApp(controller=controller)
        captured = []
        app._submit_user_text = lambda text: captured.append(text)  # type: ignore[attr-defined]

        async with app.run_test() as pilot:
            await pilot.pause()
            widget = app.query_one("#chat-input")
            widget.load_text("第一行")
            await pilot.press("ctrl+j")
            await pilot.pause()

            assert "\n" in widget.text
            assert captured == []

    asyncio.run(_run())
