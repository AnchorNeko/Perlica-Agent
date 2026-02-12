from __future__ import annotations

from pathlib import Path

import pytest

from perlica.kernel.session_store import SessionStore


def test_session_create_list_and_current(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions.db")

    a = store.create_session(context_id="ctx-a", name="alpha", provider_locked=None)
    b = store.create_session(context_id="ctx-a", name="beta", provider_locked="codex")
    c = store.create_session(context_id="ctx-b", name="gamma", provider_locked=None)

    store.set_current_session("ctx-a", b.session_id)
    store.set_current_session("ctx-b", c.session_id)

    ctx_a = store.list_sessions("ctx-a")
    all_sessions = store.list_sessions(None)

    assert {item.session_id for item in ctx_a} == {a.session_id, b.session_id}
    assert {item.session_id for item in all_sessions} == {a.session_id, b.session_id, c.session_id}
    assert store.get_current_session("ctx-a").session_id == b.session_id


def test_session_resolve_priority_and_ambiguity(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions.db")

    one = store.create_session(context_id="ctx", name="first", provider_locked=None)
    two = store.create_session(context_id="ctx", name="second", provider_locked=None)

    assert store.resolve_session_ref("ctx", one.session_id).session_id == one.session_id
    assert store.resolve_session_ref("ctx", one.session_id[:12]).session_id == one.session_id
    assert store.resolve_session_ref("ctx", "second").session_id == two.session_id

    with pytest.raises(ValueError):
        store.resolve_session_ref("ctx", "sess_")


def test_session_provider_lock_and_messages(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions.db")

    session = store.create_session(context_id="ctx", name=None, provider_locked=None)
    locked = store.lock_provider(session.session_id, "codex")
    assert locked.provider_locked == "codex"

    with pytest.raises(ValueError):
        store.lock_provider(session.session_id, "claude")

    msg1 = store.append_message(session.session_id, "user", {"text": "hello"}, run_id="r1")
    msg2 = store.append_message(session.session_id, "assistant", {"text": "hi"}, run_id="r1")

    messages = store.list_messages(session.session_id)
    assert [m.seq for m in messages] == [msg1.seq, msg2.seq]

    summary = store.add_summary(session.session_id, covered_upto_seq=msg1.seq, summary_text="summary")
    latest = store.get_latest_summary(session.session_id)
    assert latest.summary_id == summary.summary_id
    assert latest.covered_upto_seq == msg1.seq
