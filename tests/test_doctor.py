from __future__ import annotations

from pathlib import Path

from perlica.config import initialize_project_config
from perlica.config import load_settings
from perlica.kernel.runtime import Runtime


def test_doctor_verbose_includes_failure_details(isolated_env, tmp_path: Path):
    # Create an invalid plugin manifest to force a failure in doctor output.
    initialize_project_config(workspace_dir=tmp_path)
    plugins_dir = tmp_path / ".perlica_config" / "plugins"
    bad_plugin = plugins_dir / "bad"
    bad_plugin.mkdir(parents=True, exist_ok=True)
    (bad_plugin / "plugin.toml").write_text("id = 'bad'\n", encoding="utf-8")

    settings = load_settings(context_id="doctor", workspace_dir=tmp_path)
    runtime = Runtime(settings)
    try:
        report = runtime.doctor(verbose=True)
        assert "plugin_failures" in report
        assert isinstance(report["plugin_failures"], dict)
        assert "permissions" in report
        assert "mcp_servers_loaded" in report
        assert "mcp_servers" in report
        assert "acp_adapter_status" in report
        providers = report.get("providers")
        assert isinstance(providers, dict)
        assert "claude" in providers
        assert "opencode" in providers
    finally:
        runtime.close()
