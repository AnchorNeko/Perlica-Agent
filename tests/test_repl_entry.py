from __future__ import annotations

from typer.testing import CliRunner

import perlica.cli


def test_noargs_enters_repl(monkeypatch, isolated_env):
    calls = []

    def fake_chat(provider, yes, context_id):
        calls.append((provider, yes, context_id))
        return 0

    monkeypatch.setattr(perlica.cli, "_execute_chat", fake_chat)
    runner = CliRunner()

    result = runner.invoke(perlica.cli.app, [])
    assert result.exit_code == 0
    assert len(calls) == 1


def test_help_does_not_enter_repl(monkeypatch, isolated_env):
    called = {"value": False}

    def fake_chat(provider, yes, context_id):
        called["value"] = True
        return 0

    monkeypatch.setattr(perlica.cli, "_execute_chat", fake_chat)
    runner = CliRunner()

    result = runner.invoke(perlica.cli.app, ["--help"])
    assert result.exit_code == 0
    assert called["value"] is False
    assert "Perlica 命令行 Agent" in result.stdout


def test_chat_command_enters_repl(monkeypatch, isolated_env):
    calls = []

    def fake_chat(provider, yes, context_id):
        calls.append((provider, yes, context_id))
        return 0

    monkeypatch.setattr(perlica.cli, "_execute_chat", fake_chat)
    runner = CliRunner()

    result = runner.invoke(perlica.cli.app, ["chat"])
    assert result.exit_code == 0
    assert len(calls) == 1
