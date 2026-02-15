from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from perlica.config import (
    ProjectConfigError,
    load_project_config,
    load_settings,
    save_project_config,
)
from perlica.providers.profile import ALLOWED_PROVIDER_IDS


def test_default_provider_profile_is_claude(isolated_env):
    settings = load_settings(context_id="default")
    assert settings.provider == "claude"
    assert settings.provider_profile.provider_id == "claude"
    assert settings.provider_adapter_command == sys.executable
    assert settings.provider_profile.adapter_command in {"python3", sys.executable}
    assert settings.provider_profile.adapter_args == ["-m", "perlica.providers.acp_adapter_server"]


def test_default_profiles_include_opencode(isolated_env):
    config = load_project_config(workspace_dir=Path(isolated_env["workspace"]))
    assert tuple(ALLOWED_PROVIDER_IDS) == ("claude", "opencode")
    assert "opencode" in config.provider_profiles
    profile = config.provider_profiles["opencode"]
    assert profile.provider_id == "opencode"
    assert profile.adapter_command == "opencode"
    assert profile.adapter_args == ["acp"]
    assert profile.supports_mcp_config is True
    assert profile.supports_skill_config is True
    assert profile.tool_execution_mode == "provider_managed"
    assert profile.injection_failure_policy == "degrade"


def test_removed_backend_config_fails_fast(isolated_env):
    workspace = Path(isolated_env["workspace"])
    config_file = workspace / ".perlica_config" / "config.toml"
    text = config_file.read_text(encoding="utf-8")
    text = text.replace(
        "[providers.claude]\nenabled = true",
        "[providers.claude]\nenabled = true\nbackend = \"legacy_cli\"",
    )
    config_file.write_text(text, encoding="utf-8")

    with pytest.raises(ProjectConfigError) as exc_info:
        load_project_config(workspace_dir=workspace)
    assert "providers.claude.backend" in str(exc_info.value)


def test_removed_fallback_config_fails_fast(isolated_env):
    workspace = Path(isolated_env["workspace"])
    config_file = workspace / ".perlica_config" / "config.toml"
    text = config_file.read_text(encoding="utf-8")
    text += "\n[providers.claude.fallback]\nenabled = true\n"
    config_file.write_text(text, encoding="utf-8")

    with pytest.raises(ProjectConfigError) as exc_info:
        load_project_config(workspace_dir=workspace)
    assert "providers.claude.fallback" in str(exc_info.value)


def test_profile_capabilities_roundtrip_via_config(isolated_env):
    workspace = Path(isolated_env["workspace"])
    config = load_project_config(workspace_dir=workspace)
    config.provider_profiles["claude"] = replace(
        config.provider_profiles["claude"],
        supports_mcp_config=False,
        supports_skill_config=False,
        tool_execution_mode="provider_managed",
        injection_failure_policy="fail",
    )
    save_project_config(config, workspace_dir=workspace)

    loaded = load_project_config(workspace_dir=workspace)
    profile = loaded.provider_profiles["claude"]
    assert profile.supports_mcp_config is False
    assert profile.supports_skill_config is False
    assert profile.tool_execution_mode == "provider_managed"
    assert profile.injection_failure_policy == "fail"


def test_unknown_provider_override_falls_back_to_default_profile(isolated_env):
    settings = load_settings(context_id="default", provider="codex")
    assert settings.provider == "claude"
    assert settings.provider_profile.provider_id == "claude"
