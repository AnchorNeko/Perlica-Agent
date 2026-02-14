from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import perlica.cli as cli
import perlica.repl as repl
from perlica.config import load_project_config


class _FakeTTYInput:
    def __init__(self, lines: list[str]):
        self._lines = list(lines)

    def readline(self) -> str:
        if self._lines:
            return self._lines.pop(0)
        return ""

    def isatty(self) -> bool:
        return True


def test_repl_tty_first_selection_persists_provider(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(repl, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", _FakeTTYInput(["2\n"]))

    captured: list[tuple[str, bool, str | None]] = []

    def _fake_start_tui_chat(provider, yes, context_id):
        captured.append((provider, yes, context_id))
        return 0

    monkeypatch.setattr(repl, "start_tui_chat", _fake_start_tui_chat)

    code = repl.start_repl(
        provider=None,
        yes=True,
        context_id="default",
        run_executor=lambda *_args, **_kwargs: 0,
        stream=StringIO(),
        err_stream=StringIO(),
    )

    assert code == 0
    assert captured == [("opencode", True, "default")]

    config = load_project_config(workspace_dir=tmp_path)
    assert config.provider_selected is True
    assert config.default_provider == "opencode"


def test_cli_first_selection_requires_provider_when_non_tty(isolated_env, monkeypatch):
    workspace = Path(isolated_env["workspace"])
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False)

    selected, code = cli._resolve_provider_with_first_selection(None)
    assert code == 2
    assert selected is None


def test_cli_explicit_provider_marks_first_selection_done(isolated_env, monkeypatch):
    workspace = Path(isolated_env["workspace"])
    monkeypatch.chdir(workspace)

    selected, code = cli._resolve_provider_with_first_selection("opencode")
    assert code == 0
    assert selected == "opencode"

    config = load_project_config(workspace_dir=workspace)
    assert config.provider_selected is True
    assert config.default_provider == "opencode"
