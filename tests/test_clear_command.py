from __future__ import annotations

from io import StringIO

from perlica.config import load_settings
from perlica.kernel.runtime import Runtime
from perlica.repl_commands import ReplState, dispatch_slash_command


def _state() -> ReplState:
    return ReplState(
        context_id="default",
        provider=None,
        yes=True,
        session_ref=None,
        session_name=None,
        session_is_ephemeral=False,
    )


def test_clear_command_clears_messages_and_summaries_but_keeps_session(isolated_env):
    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    stream = StringIO()
    try:
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            name="clear-demo",
            provider_locked="claude",
            is_ephemeral=False,
        )
        runtime.session_store.set_current_session(runtime.context_id, session.session_id)
        runtime.session_store.append_message(
            session.session_id,
            "user",
            {"text": "hello"},
            run_id="seed",
        )
        runtime.session_store.append_message(
            session.session_id,
            "assistant",
            {"text": "world"},
            run_id="seed",
        )
        runtime.session_store.add_summary(
            session.session_id,
            covered_upto_seq=2,
            summary_text="summary",
        )
    finally:
        runtime.close()

    state = _state()
    state.session_ref = session.session_id
    state.session_name = session.name
    result = dispatch_slash_command("/clear", state=state, stream=stream)
    assert result.handled is True
    assert "已清空当前会话上下文" in stream.getvalue()

    runtime = Runtime(load_settings(context_id="default"))
    try:
        kept = runtime.session_store.get_session(session.session_id)
        assert kept is not None
        assert kept.provider_locked == "claude"
        assert runtime.session_store.list_messages(session.session_id) == []
        assert runtime.session_store.get_latest_summary(session.session_id) is None
    finally:
        runtime.close()

