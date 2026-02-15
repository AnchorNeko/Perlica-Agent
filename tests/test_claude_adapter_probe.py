from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from perlica.config import load_project_config, load_settings, save_project_config
from perlica.kernel.runtime import Runtime


def test_doctor_reports_missing_adapter_command(isolated_env):
    workspace = Path(isolated_env["workspace"])
    config = load_project_config(workspace_dir=workspace)
    config.provider_profiles["claude"] = replace(
        config.provider_profiles["claude"],
        adapter_command="/definitely/missing/claude-code-acp",
    )
    save_project_config(config, workspace_dir=workspace)

    settings = load_settings(context_id="default", workspace_dir=workspace)
    runtime = Runtime(settings)
    try:
        report = runtime.doctor(verbose=False)
        assert report["provider_adapter_probe"] == "missing_command"
        assert report["active_provider"] == "claude"
    finally:
        runtime.close()


def test_doctor_reports_configured_for_existing_absolute_command(isolated_env):
    workspace = Path(isolated_env["workspace"])
    config = load_project_config(workspace_dir=workspace)
    config.provider_profiles["claude"] = replace(
        config.provider_profiles["claude"],
        adapter_command="/bin/sh",
    )
    save_project_config(config, workspace_dir=workspace)

    settings = load_settings(context_id="default", workspace_dir=workspace)
    runtime = Runtime(settings)
    try:
        report = runtime.doctor(verbose=False)
        assert report["provider_adapter_probe"] == "configured"
    finally:
        runtime.close()
