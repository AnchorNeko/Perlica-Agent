from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import perlica.cli


def _combined_output(result) -> str:
    try:
        return result.stdout + result.stderr
    except Exception:
        return result.stdout


def test_run_requires_project_config(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(perlica.cli.app, ["run", "hello"])

    assert result.exit_code == 2
    output = _combined_output(result)
    assert "perlica init" in output


def test_help_and_init_work_without_config(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    help_result = runner.invoke(perlica.cli.app, ["--help"])
    assert help_result.exit_code == 0

    init_result = runner.invoke(perlica.cli.app, ["init"])
    assert init_result.exit_code == 0

    config_root = tmp_path / ".perlica_config"
    assert (config_root / "config.toml").is_file()
    assert (config_root / "skills").is_dir()
    assert (config_root / "plugins").is_dir()
    assert (config_root / "prompts").is_dir()
    assert (config_root / "prompts" / "system.md").is_file()
    assert (config_root / "mcp").is_dir()
    assert (config_root / "mcp" / "servers.toml").is_file()
    assert (config_root / "contexts" / "default" / "logs").is_dir()
    assert (config_root / "contexts" / "default" / "eventlog.db").is_file()
    assert (config_root / "contexts" / "default" / "approvals.db").is_file()
    assert (config_root / "contexts" / "default" / "sessions.db").is_file()


def test_init_fails_when_config_exists_without_force(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    first = runner.invoke(perlica.cli.app, ["init"])
    assert first.exit_code == 0

    second = runner.invoke(perlica.cli.app, ["init"])
    assert second.exit_code == 2
    output = _combined_output(second)
    assert "already exists" in output


def test_init_force_recreates_project_config(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    first = runner.invoke(perlica.cli.app, ["init"])
    assert first.exit_code == 0

    marker = tmp_path / ".perlica_config" / "skills" / "marker.txt"
    marker.write_text("old", encoding="utf-8")

    rebuilt = runner.invoke(perlica.cli.app, ["init", "--force"])
    assert rebuilt.exit_code == 0
    assert not marker.exists()
