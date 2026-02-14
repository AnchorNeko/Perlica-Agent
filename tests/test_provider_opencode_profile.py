from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from perlica.config import load_project_config, load_settings, save_project_config


def test_load_settings_with_opencode_profile(isolated_env):
    workspace = Path(isolated_env["workspace"])
    config = load_project_config(workspace_dir=workspace)
    config.default_provider = "opencode"
    config.provider_selected = True
    save_project_config(config, workspace_dir=workspace)

    settings = load_settings(context_id="default", workspace_dir=workspace)
    assert settings.provider == "opencode"
    assert settings.provider_profile.provider_id == "opencode"
    assert settings.provider_profile.backend == "acp"
    assert settings.provider_adapter_command == "opencode"
    assert settings.provider_adapter_args == ["acp"]


def test_opencode_profile_can_be_disabled_in_config(isolated_env):
    workspace = Path(isolated_env["workspace"])
    config = load_project_config(workspace_dir=workspace)
    config.provider_profiles["opencode"] = replace(
        config.provider_profiles["opencode"],
        enabled=False,
    )
    save_project_config(config, workspace_dir=workspace)

    loaded = load_project_config(workspace_dir=workspace)
    assert loaded.provider_profiles["opencode"].enabled is False
