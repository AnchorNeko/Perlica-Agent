from __future__ import annotations

import json

from typer.testing import CliRunner

import perlica.cli


def _combined_output(result) -> str:
    try:
        return result.stdout + result.stderr
    except Exception:
        return result.stdout


def test_cli_route_equivalence(monkeypatch, isolated_env):
    calls = []

    def fake_execute(text, provider, yes, context_id, session_ref):
        calls.append((text, provider, yes, context_id, session_ref))
        return 0

    monkeypatch.setattr(perlica.cli, "_execute_prompt", fake_execute)
    runner = CliRunner()

    direct = runner.invoke(perlica.cli.app, ["hello", "world"])
    explicit = runner.invoke(perlica.cli.app, ["run", "hello world"])

    assert direct.exit_code == 0
    assert explicit.exit_code == 0
    assert calls[0][0] == "hello world"
    assert calls[1][0] == "hello world"
    assert calls[0][4] is None
    assert calls[1][4] is None


def test_doctor_outputs_json(monkeypatch, isolated_env):
    runner = CliRunner()
    result = runner.invoke(perlica.cli.app, ["doctor"])

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert "providers" in parsed
    assert "plugins_loaded" in parsed


def test_approvals_reset_validation(monkeypatch, isolated_env):
    runner = CliRunner()
    result = runner.invoke(perlica.cli.app, ["policy", "approvals", "reset"])
    assert result.exit_code == 2
    output = _combined_output(result)
    assert "--tool" in output and "--risk" in output


def test_run_allows_default_provider_without_flag(monkeypatch, isolated_env):
    calls = []

    def fake_execute(text, provider, yes, context_id, session_ref):
        calls.append((text, provider, yes, context_id, session_ref))
        return 0

    monkeypatch.setattr(perlica.cli, "_execute_prompt", fake_execute)
    runner = CliRunner()
    result = runner.invoke(perlica.cli.app, ["run", "hello"])
    assert result.exit_code == 0
    assert calls
    assert calls[0][1] is None


def test_chat_allows_default_provider_without_flag(monkeypatch, isolated_env):
    captured = {}

    def fake_execute(provider, yes, context_id):
        captured["provider"] = provider
        captured["yes"] = yes
        captured["context_id"] = context_id
        return 0

    monkeypatch.setattr(perlica.cli, "_execute_chat", fake_execute)
    runner = CliRunner()
    result = runner.invoke(perlica.cli.app, ["chat"])
    assert result.exit_code == 0
    assert captured["provider"] is None
