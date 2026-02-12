from __future__ import annotations

from io import StringIO

import perlica.repl as repl


def test_start_repl_tty_routes_to_tui(monkeypatch, isolated_env):
    calls = []

    monkeypatch.setattr(repl, "_stdin_is_tty", lambda: True)

    def fake_start_tui_chat(provider, yes, context_id):
        calls.append((provider, yes, context_id))
        return 0

    monkeypatch.setattr(repl, "start_tui_chat", fake_start_tui_chat)

    code = repl.start_repl(
        provider="claude",
        yes=True,
        context_id="default",
        run_executor=lambda *_args, **_kwargs: 0,
        stream=StringIO(),
        err_stream=StringIO(),
    )

    assert code == 0
    assert calls == [("claude", True, "default")]


def test_start_repl_tty_tui_failure_returns_nonzero(monkeypatch, isolated_env):
    monkeypatch.setattr(repl, "_stdin_is_tty", lambda: True)

    def fake_start_tui_chat(provider, yes, context_id):
        raise RuntimeError("textual unavailable")

    monkeypatch.setattr(repl, "start_tui_chat", fake_start_tui_chat)

    out = StringIO()
    err = StringIO()
    code = repl.start_repl(
        provider="claude",
        yes=False,
        context_id=None,
        run_executor=lambda *_args, **_kwargs: 0,
        stream=out,
        err_stream=err,
    )

    assert code == 1
    assert "textual unavailable" in err.getvalue()
