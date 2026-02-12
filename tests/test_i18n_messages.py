from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import perlica.cli


def _combined_output(result) -> str:
    try:
        return result.stdout + result.stderr
    except Exception:
        return result.stdout


def test_help_contains_chinese(isolated_env):
    runner = CliRunner()
    result = runner.invoke(perlica.cli.app, ["--help"])
    assert result.exit_code == 0
    assert "命令行 Agent" in result.stdout
    assert "技能管理" in result.stdout


def test_missing_config_message_chinese(monkeypatch, tmp_path: Path):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(perlica.cli.app, ["run", "你好"])
    assert result.exit_code == 2
    output = _combined_output(result)
    assert "当前目录缺少项目配置目录" in output
    assert "perlica init" in output
