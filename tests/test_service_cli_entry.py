from __future__ import annotations

from io import StringIO

from typer.testing import CliRunner

import perlica.cli
import perlica.repl as repl


def test_cli_service_flag_routes_to_service(monkeypatch, isolated_env):
    calls = []

    def fake_execute_service(provider, yes, context_id):
        calls.append((provider, yes, context_id))
        return 0

    monkeypatch.setattr(perlica.cli, "_execute_service", fake_execute_service)
    runner = CliRunner()

    result = runner.invoke(perlica.cli.app, ["--service"])
    assert result.exit_code == 0
    assert len(calls) == 1


def test_start_service_mode_requires_tty(monkeypatch, isolated_env):
    monkeypatch.setattr(repl, "_stdin_is_tty", lambda: False)

    code = repl.start_service_mode(
        provider="claude",
        yes=False,
        context_id=None,
        stream=StringIO(),
        err_stream=StringIO(),
    )

    assert code == 2


def test_cli_service_flag_without_tty_returns_tty_error(isolated_env):
    runner = CliRunner()
    result = runner.invoke(perlica.cli.app, ["--service"])
    assert result.exit_code == 2
