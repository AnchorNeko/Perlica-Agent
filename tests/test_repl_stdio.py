from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

from perlica.repl import start_repl


class FakeStdin:
    def __init__(self, payload: str, tty: bool):
        self._payload = payload
        self._tty = tty

    def read(self) -> str:
        return self._payload

    def isatty(self) -> bool:
        return self._tty


def test_non_tty_repl_reads_stdin_once(isolated_env, monkeypatch):
    calls = []

    def fake_run(text, provider, yes, context_id, session_ref):
        calls.append((text, provider, yes, context_id, session_ref))
        return 0

    monkeypatch.setattr(sys, "stdin", FakeStdin("你好", tty=False))
    out = StringIO()
    err = StringIO()

    exit_code = start_repl(
        provider="claude",
        yes=True,
        context_id=None,
        run_executor=fake_run,
        stream=out,
        err_stream=err,
    )

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0][0] == "你好"
    assert calls[0][1] == "claude"


def test_non_tty_repl_empty_stdin_returns_error(isolated_env, monkeypatch):
    monkeypatch.setattr(sys, "stdin", FakeStdin("", tty=False))
    out = StringIO()
    err = StringIO()

    exit_code = start_repl(
        provider="claude",
        yes=True,
        context_id=None,
        run_executor=lambda *_args, **_kwargs: 0,
        stream=out,
        err_stream=err,
    )

    assert exit_code == 2
    assert "未检测到输入内容" in err.getvalue()


def test_repl_auto_init_when_config_missing(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", FakeStdin("hello", tty=False))
    calls = []

    def fake_run(text, provider, yes, context_id, session_ref):
        calls.append(text)
        return 0

    out = StringIO()
    err = StringIO()
    exit_code = start_repl(
        provider="claude",
        yes=False,
        context_id=None,
        run_executor=fake_run,
        stream=out,
        err_stream=err,
    )

    assert exit_code == 0
    assert calls == ["hello"]
    assert (tmp_path / ".perlica_config" / "config.toml").is_file()


def test_non_tty_repl_uses_default_provider_when_missing(isolated_env, monkeypatch):
    monkeypatch.setattr(sys, "stdin", FakeStdin("hello", tty=False))
    calls = []
    out = StringIO()
    err = StringIO()

    def fake_run(text, provider, yes, context_id, session_ref):
        calls.append((text, provider, yes, context_id, session_ref))
        return 0

    exit_code = start_repl(
        provider=None,
        yes=True,
        context_id=None,
        run_executor=fake_run,
        stream=out,
        err_stream=err,
    )

    assert exit_code == 0
    assert calls
    assert calls[0][1] == "claude"
