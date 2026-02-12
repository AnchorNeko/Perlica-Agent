from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import perlica.cli


def test_doctor_json_format(isolated_env):
    runner = CliRunner()
    result = runner.invoke(perlica.cli.app, ["doctor", "--format", "json"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert "providers" in parsed
    assert "plugins_loaded" in parsed
    assert "permissions" in parsed
    assert "mcp_servers_loaded" in parsed
    assert "system_prompt_loaded" in parsed
    assert "provider_backend" in parsed
    assert "acp_adapter_status" in parsed


def test_doctor_text_format(isolated_env):
    runner = CliRunner()
    result = runner.invoke(perlica.cli.app, ["doctor", "--format", "text"])
    assert result.exit_code == 0
    assert "系统诊断 (Doctor Report)" in result.stdout
    assert "Provider 可用性 (Provider Availability)" in result.stdout
    assert "权限检查 (Permission Checks)" in result.stdout
    assert "MCP" in result.stdout


def test_doctor_text_verbose_has_failure_details(isolated_env):
    workspace = Path(isolated_env["workspace"])
    bad_plugin = workspace / ".perlica_config" / "plugins" / "bad"
    bad_plugin.mkdir(parents=True, exist_ok=True)
    (bad_plugin / "plugin.toml").write_text("id='bad'\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(perlica.cli.app, ["doctor", "--format", "text", "--verbose"])
    assert result.exit_code == 0
    assert "插件失败详情 (Plugin Failures)" in result.stdout
