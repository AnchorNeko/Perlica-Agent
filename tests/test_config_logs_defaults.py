from __future__ import annotations

from perlica.config import (
    DEFAULT_LOGS_ENABLED,
    DEFAULT_LOGS_FORMAT,
    DEFAULT_LOGS_MAX_FILE_BYTES,
    DEFAULT_LOGS_MAX_FILES,
    DEFAULT_LOGS_REDACTION,
    initialize_project_config,
    load_project_config,
    load_settings,
)


def test_init_config_contains_runtime_logs_defaults(tmp_path):
    config_root = initialize_project_config(workspace_dir=tmp_path)
    config = load_project_config(workspace_dir=tmp_path)
    config_text = (config_root / "config.toml").read_text(encoding="utf-8")

    assert "[runtime.logs]" in config_text
    assert "enabled = true" in config_text
    assert 'format = "jsonl"' in config_text
    assert "max_file_bytes = 10485760" in config_text
    assert "max_files = 5" in config_text
    assert 'redaction = "default"' in config_text

    assert config.logs_enabled is DEFAULT_LOGS_ENABLED
    assert config.logs_format == DEFAULT_LOGS_FORMAT
    assert config.logs_max_file_bytes == DEFAULT_LOGS_MAX_FILE_BYTES
    assert config.logs_max_files == DEFAULT_LOGS_MAX_FILES
    assert config.logs_redaction == DEFAULT_LOGS_REDACTION


def test_legacy_config_without_runtime_logs_uses_defaults(tmp_path):
    config_root = initialize_project_config(workspace_dir=tmp_path)
    config_file = config_root / "config.toml"
    config_file.write_text(
        "\n".join(
            [
                "[model]",
                'default_provider = "codex"',
                "",
                "[runtime]",
                "max_tool_calls = 8",
                "context_budget_ratio = 0.8",
                "max_summary_attempts = 3",
                "",
                "[runtime.provider_context_windows]",
                "codex = 200000",
                "claude = 200000",
                "",
                "[context]",
                'default_id = "default"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(context_id="legacy", workspace_dir=tmp_path)
    assert settings.logs_enabled is DEFAULT_LOGS_ENABLED
    assert settings.logs_format == DEFAULT_LOGS_FORMAT
    assert settings.logs_max_file_bytes == DEFAULT_LOGS_MAX_FILE_BYTES
    assert settings.logs_max_files == DEFAULT_LOGS_MAX_FILES
    assert settings.logs_redaction == DEFAULT_LOGS_REDACTION


def test_invalid_runtime_logs_values_fallback_to_defaults(tmp_path):
    config_root = initialize_project_config(workspace_dir=tmp_path)
    config_file = config_root / "config.toml"
    config_file.write_text(
        "\n".join(
            [
                "[model]",
                'default_provider = "codex"',
                "",
                "[runtime]",
                "max_tool_calls = 8",
                "context_budget_ratio = 0.8",
                "max_summary_attempts = 3",
                "",
                "[runtime.provider_context_windows]",
                "codex = 200000",
                "claude = 200000",
                "",
                "[runtime.logs]",
                'enabled = "maybe"',
                'format = "xml"',
                "max_file_bytes = -1",
                "max_files = 0",
                'redaction = "unknown"',
                "",
                "[context]",
                'default_id = "default"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(context_id="legacy", workspace_dir=tmp_path)
    assert settings.logs_enabled is DEFAULT_LOGS_ENABLED
    assert settings.logs_format == DEFAULT_LOGS_FORMAT
    assert settings.logs_max_file_bytes == DEFAULT_LOGS_MAX_FILE_BYTES
    assert settings.logs_max_files == DEFAULT_LOGS_MAX_FILES
    assert settings.logs_redaction == DEFAULT_LOGS_REDACTION
