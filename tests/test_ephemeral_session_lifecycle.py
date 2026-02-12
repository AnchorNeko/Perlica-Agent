from __future__ import annotations

from pathlib import Path

from perlica.config import load_settings
from perlica.kernel.runtime import Runtime
from perlica.kernel.session_store import SessionStore
from perlica.tui.controller import ChatController


def test_ephemeral_session_hidden_by_default(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions.db")
    persistent = store.create_session(context_id="ctx", name="persist", is_ephemeral=False)
    ephemeral = store.create_session(context_id="ctx", is_ephemeral=True)

    listed_default = store.list_sessions(context_id="ctx")
    listed_all = store.list_sessions(context_id="ctx", include_ephemeral=True)

    assert {x.session_id for x in listed_default} == {persistent.session_id}
    assert {x.session_id for x in listed_all} == {persistent.session_id, ephemeral.session_id}


def test_ephemeral_save_discard_cleanup(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions.db")
    session = store.create_session(context_id="ctx", is_ephemeral=True)
    store.set_current_session("ctx", session.session_id)
    store.append_message(session.session_id, "user", {"text": "hello"}, run_id="r1")

    saved = store.save_session(session.session_id, name="saved-chat")
    assert saved.is_ephemeral is False
    assert saved.saved_at_ms is not None
    assert saved.name == "saved-chat"

    unsaved = store.create_session(context_id="ctx", is_ephemeral=True)
    store.append_message(unsaved.session_id, "user", {"text": "tmp"}, run_id="r2")
    store.add_summary(unsaved.session_id, covered_upto_seq=1, summary_text="sum")

    cleaned = store.cleanup_unsaved_ephemeral(context_id="ctx")
    assert cleaned == 1
    assert store.get_session(unsaved.session_id) is None
    assert store.get_session(saved.session_id) is not None

    store.discard_session(saved.session_id)
    assert store.get_session(saved.session_id) is None


def test_chat_controller_startup_cleans_old_unsaved_ephemeral(isolated_env):
    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    try:
        stale = runtime.session_store.create_session(context_id=runtime.context_id, is_ephemeral=True)
        runtime.session_store.append_message(stale.session_id, "user", {"text": "stale"}, run_id="seed")
        stale_id = stale.session_id
    finally:
        runtime.close()

    controller = ChatController(provider="claude", yes=True, context_id="default")
    try:
        fresh_id = controller.state.session_ref
        settings2 = load_settings(context_id="default")
        runtime2 = Runtime(settings2)
        try:
            all_sessions = runtime2.session_store.list_sessions(
                context_id="default",
                include_ephemeral=True,
            )
            ids = {x.session_id for x in all_sessions}
            assert stale_id not in ids
            assert fresh_id in ids
        finally:
            runtime2.close()
    finally:
        controller.close()
