from __future__ import annotations

from typer.testing import CliRunner

import perlica.cli

def test_model_command_removed(isolated_env):
    runner = CliRunner()
    result = runner.invoke(perlica.cli.app, ["model", "get"])
    assert result.exit_code == 2
    combined = result.output
    assert "No such command 'model'" in combined
