from __future__ import annotations

from perlica.tui.controller import ChatController


def test_tui_slash_removed_model_and_save_and_list(isolated_env):
    controller = ChatController(provider="claude", yes=True, context_id="default")
    try:
        model = controller.run_slash_command("/model set claude")
        assert model.handled is True
        assert "命令 `/model` 已移除" in model.output_text

        saved = controller.run_slash_command("/save demo")
        assert saved.handled is True
        assert "会话已保存" in saved.output_text

        listed = controller.run_slash_command("/session list --all")
        assert listed.handled is True
        assert "ephemeral=" in listed.output_text
        assert "provider=claude" in listed.output_text
    finally:
        controller.close()


def test_tui_slash_discard_unsaved_ephemeral(isolated_env):
    controller = ChatController(provider="claude", yes=True, context_id="default")
    try:
        before = controller.state.session_ref
        discarded = controller.run_slash_command("/discard")
        assert discarded.handled is True
        assert "已丢弃临时会话并创建新临时会话" in discarded.output_text
        assert controller.state.session_ref != before
    finally:
        controller.close()


def test_tui_unknown_slash_falls_back_to_text(isolated_env):
    controller = ChatController(provider="claude", yes=True, context_id="default")
    try:
        outcome = controller.run_slash_command("/foo bar")
        assert outcome.handled is False
        assert outcome.fallback_text == "/foo bar"
    finally:
        controller.close()
