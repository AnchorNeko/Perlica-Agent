"""Configuration loading and directory resolution for Perlica."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence

try:  # pragma: no cover - exercised on Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - fallback for Python 3.9/3.10
    import tomli as tomllib  # type: ignore[no-redef]

from perlica.providers.profile import (
    ALLOWED_PROVIDER_BACKENDS,
    ALLOWED_PROVIDER_IDS as PROFILE_ALLOWED_PROVIDER_IDS,
    DEFAULT_ADAPTER_ARGS,
    DEFAULT_ADAPTER_COMMAND,
    DEFAULT_PROVIDER_BACKEND,
    DEFAULT_PROVIDER_ID,
    ProviderProfile,
    default_provider_profiles,
)

CONFIG_DIR_NAME = ".perlica_config"
CONFIG_FILE_NAME = "config.toml"
PROMPTS_DIR_NAME = "prompts"
SYSTEM_PROMPT_FILE_NAME = "system.md"
MCP_DIR_NAME = "mcp"
MCP_SERVERS_FILE_NAME = "servers.toml"

DEFAULT_CONTEXT_ID = "default"
ALLOWED_PROVIDERS = tuple(PROFILE_ALLOWED_PROVIDER_IDS)
DEFAULT_ACP_CONNECT_TIMEOUT_SEC = 10
DEFAULT_ACP_REQUEST_TIMEOUT_SEC = 60
DEFAULT_ACP_MAX_RETRIES = 2
DEFAULT_ACP_BACKOFF = "exponential+jitter"
DEFAULT_ACP_CIRCUIT_BREAKER_ENABLED = True

DEFAULT_MAX_TOOL_CALLS = 8
DEFAULT_PROVIDER_CONTEXT_WINDOWS = {
    "claude": 200000,
    "opencode": 200000,
}
DEFAULT_CONTEXT_BUDGET_RATIO = 0.8
DEFAULT_MAX_SUMMARY_ATTEMPTS = 3
DEFAULT_LOGS_ENABLED = True
DEFAULT_LOGS_FORMAT = "jsonl"
DEFAULT_LOGS_MAX_FILE_BYTES = 10 * 1024 * 1024
DEFAULT_LOGS_MAX_FILES = 5
DEFAULT_LOGS_REDACTION = "default"
ALLOWED_LOG_FORMATS = ("jsonl",)
ALLOWED_LOG_REDACTION = ("default", "none", "strict")


class ProjectConfigError(RuntimeError):
    """Raised when project configuration is missing or invalid."""


@dataclass
class ProjectConfig:
    default_provider: str = DEFAULT_PROVIDER_ID
    provider_selected: bool = True
    provider_profiles: Dict[str, ProviderProfile] = field(default_factory=default_provider_profiles)
    provider_backend: str = DEFAULT_PROVIDER_BACKEND
    provider_adapter_command: str = DEFAULT_ADAPTER_COMMAND
    provider_adapter_args: List[str] = field(default_factory=lambda: list(DEFAULT_ADAPTER_ARGS))
    provider_adapter_env_allowlist: List[str] = field(default_factory=list)
    provider_acp_connect_timeout: int = DEFAULT_ACP_CONNECT_TIMEOUT_SEC
    provider_acp_request_timeout: int = DEFAULT_ACP_REQUEST_TIMEOUT_SEC
    provider_acp_max_retries: int = DEFAULT_ACP_MAX_RETRIES
    provider_acp_backoff: str = DEFAULT_ACP_BACKOFF
    provider_acp_circuit_breaker_enabled: bool = DEFAULT_ACP_CIRCUIT_BREAKER_ENABLED
    default_context_id: str = DEFAULT_CONTEXT_ID
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    context_budget_ratio: float = DEFAULT_CONTEXT_BUDGET_RATIO
    max_summary_attempts: int = DEFAULT_MAX_SUMMARY_ATTEMPTS
    provider_context_windows: Dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_PROVIDER_CONTEXT_WINDOWS)
    )
    logs_enabled: bool = DEFAULT_LOGS_ENABLED
    logs_format: str = DEFAULT_LOGS_FORMAT
    logs_max_file_bytes: int = DEFAULT_LOGS_MAX_FILE_BYTES
    logs_max_files: int = DEFAULT_LOGS_MAX_FILES
    logs_redaction: str = DEFAULT_LOGS_REDACTION


@dataclass
class Settings:
    """Resolved runtime settings for one CLI invocation."""

    project_root: Path
    config_root: Path
    context_id: str = DEFAULT_CONTEXT_ID
    provider: str = DEFAULT_PROVIDER_ID
    provider_profile: ProviderProfile = field(
        default_factory=lambda: default_provider_profiles()[DEFAULT_PROVIDER_ID]
    )
    provider_backend: str = DEFAULT_PROVIDER_BACKEND
    provider_adapter_command: str = DEFAULT_ADAPTER_COMMAND
    provider_adapter_args: List[str] = field(default_factory=lambda: list(DEFAULT_ADAPTER_ARGS))
    provider_adapter_env_allowlist: List[str] = field(default_factory=list)
    provider_acp_connect_timeout: int = DEFAULT_ACP_CONNECT_TIMEOUT_SEC
    provider_acp_request_timeout: int = DEFAULT_ACP_REQUEST_TIMEOUT_SEC
    provider_acp_max_retries: int = DEFAULT_ACP_MAX_RETRIES
    provider_acp_backoff: str = DEFAULT_ACP_BACKOFF
    provider_acp_circuit_breaker_enabled: bool = DEFAULT_ACP_CIRCUIT_BREAKER_ENABLED
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    workspace_dir: Path = field(default_factory=Path.cwd)
    plugin_dirs: List[Path] = field(default_factory=list)
    skill_dirs: List[Path] = field(default_factory=list)
    provider_context_windows: Dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_PROVIDER_CONTEXT_WINDOWS)
    )
    context_budget_ratio: float = DEFAULT_CONTEXT_BUDGET_RATIO
    max_summary_attempts: int = DEFAULT_MAX_SUMMARY_ATTEMPTS
    logs_enabled: bool = DEFAULT_LOGS_ENABLED
    logs_format: str = DEFAULT_LOGS_FORMAT
    logs_max_file_bytes: int = DEFAULT_LOGS_MAX_FILE_BYTES
    logs_max_files: int = DEFAULT_LOGS_MAX_FILES
    logs_redaction: str = DEFAULT_LOGS_REDACTION

    @property
    def contexts_root(self) -> Path:
        return self.config_root / "contexts"

    @property
    def context_dir(self) -> Path:
        return self.contexts_root / self.context_id

    @property
    def prompts_root(self) -> Path:
        return self.config_root / PROMPTS_DIR_NAME

    @property
    def system_prompt_file(self) -> Path:
        return self.prompts_root / SYSTEM_PROMPT_FILE_NAME

    @property
    def mcp_root(self) -> Path:
        return self.config_root / MCP_DIR_NAME

    @property
    def mcp_servers_file(self) -> Path:
        return self.mcp_root / MCP_SERVERS_FILE_NAME


def resolve_project_root(workspace_dir: Optional[Path] = None) -> Path:
    return (workspace_dir or Path.cwd()).resolve()


def resolve_project_config_root(workspace_dir: Optional[Path] = None) -> Path:
    return resolve_project_root(workspace_dir) / CONFIG_DIR_NAME


def project_config_exists(workspace_dir: Optional[Path] = None) -> bool:
    config_root = resolve_project_config_root(workspace_dir)
    return config_root.is_dir() and (config_root / CONFIG_FILE_NAME).is_file()


def _dedupe_paths(paths: List[Path]) -> List[Path]:
    result: List[Path] = []
    seen = set()
    for path in paths:
        normalized = str(path.expanduser().resolve())
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(Path(normalized))
    return result


def _discover_plugin_dirs(settings: Settings) -> List[Path]:
    config_plugins = settings.config_root / "plugins"
    context_plugins = settings.context_dir / "plugins"
    return _dedupe_paths([config_plugins, context_plugins])


def _discover_skill_dirs(settings: Settings) -> List[Path]:
    config_skills = settings.config_root / "skills"

    plugin_skill_dirs: List[Path] = []
    for plugin_root in settings.plugin_dirs:
        if not plugin_root.exists() or not plugin_root.is_dir():
            continue
        for plugin_dir in plugin_root.iterdir():
            if not plugin_dir.is_dir():
                continue
            plugin_skill_dirs.append(plugin_dir / "skills")

    return _dedupe_paths([config_skills] + plugin_skill_dirs)


def _normalize_provider(provider: object) -> str:
    candidate = str(provider or DEFAULT_PROVIDER_ID).strip().lower() or DEFAULT_PROVIDER_ID
    if candidate not in PROFILE_ALLOWED_PROVIDER_IDS:
        return DEFAULT_PROVIDER_ID
    return candidate


def _normalize_provider_backend(value: object) -> str:
    candidate = str(value or DEFAULT_PROVIDER_BACKEND).strip().lower()
    if candidate not in ALLOWED_PROVIDER_BACKENDS:
        return DEFAULT_PROVIDER_BACKEND
    return candidate


def _safe_positive_int(value: object, default: int) -> int:
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, converted)


def _safe_positive_int_or_default(value: object, default: int) -> int:
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return default
    if converted <= 0:
        return default
    return converted


def _safe_ratio(value: object, default: float) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return default
    if converted <= 0:
        return default
    if converted > 1:
        return 1.0
    return converted


def _safe_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _safe_log_format(value: object, default: str) -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in ALLOWED_LOG_FORMATS:
        return default
    return normalized


def _safe_redaction(value: object, default: str) -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in ALLOWED_LOG_REDACTION:
        return default
    return normalized


def _safe_string_list(value: object, default: Sequence[str]) -> List[str]:
    if not isinstance(value, list):
        return list(default)
    result: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        result.append(text)
    if not result:
        return list(default)
    return result


def _safe_backoff(value: object, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    return text


def _resolve_adapter_command(command: str, args: Sequence[str]) -> str:
    text = str(command or "").strip()
    if not text:
        return text
    if text not in {"python", "python3"}:
        return text
    arg_list = [str(item or "").strip() for item in args]
    if len(arg_list) >= 2 and arg_list[0] == "-m" and arg_list[1].startswith("perlica."):
        return sys.executable
    return text


def _safe_provider_profiles(data: Dict[str, object]) -> Dict[str, ProviderProfile]:
    parsed: Dict[str, ProviderProfile] = {}
    for provider_id in PROFILE_ALLOWED_PROVIDER_IDS:
        raw = data.get(provider_id)
        if not isinstance(raw, dict):
            continue
        adapter = raw.get("adapter") if isinstance(raw.get("adapter"), dict) else {}
        acp = raw.get("acp") if isinstance(raw.get("acp"), dict) else {}
        fallback = raw.get("fallback") if isinstance(raw.get("fallback"), dict) else {}
        profile = ProviderProfile(
            provider_id=provider_id,
            enabled=_safe_bool(raw.get("enabled"), True),  # type: ignore[arg-type]
            backend=_normalize_provider_backend(raw.get("backend")),  # type: ignore[arg-type]
            adapter_command=str(adapter.get("command") or DEFAULT_ADAPTER_COMMAND).strip() or DEFAULT_ADAPTER_COMMAND,  # type: ignore[arg-type]
            adapter_args=_safe_string_list(adapter.get("args"), DEFAULT_ADAPTER_ARGS),  # type: ignore[arg-type]
            adapter_env_allowlist=_safe_string_list(adapter.get("env_allowlist"), []),  # type: ignore[arg-type]
            acp_connect_timeout_sec=_safe_positive_int(
                acp.get("connect_timeout"),  # type: ignore[arg-type]
                DEFAULT_ACP_CONNECT_TIMEOUT_SEC,
            ),
            acp_request_timeout_sec=_safe_positive_int(
                acp.get("request_timeout"),  # type: ignore[arg-type]
                DEFAULT_ACP_REQUEST_TIMEOUT_SEC,
            ),
            acp_max_retries=_safe_positive_int(
                acp.get("max_retries"),  # type: ignore[arg-type]
                DEFAULT_ACP_MAX_RETRIES,
            ),
            acp_backoff=_safe_backoff(
                acp.get("backoff"),  # type: ignore[arg-type]
                DEFAULT_ACP_BACKOFF,
            ),
            acp_circuit_breaker_enabled=_safe_bool(
                acp.get("circuit_breaker_enabled"),  # type: ignore[arg-type]
                DEFAULT_ACP_CIRCUIT_BREAKER_ENABLED,
            ),
            fallback_enabled=_safe_bool(
                fallback.get("enabled"),  # type: ignore[arg-type]
                False,
            ),
        )
        parsed[provider_id] = profile

    defaults = default_provider_profiles()
    if not parsed:
        return defaults
    merged = dict(defaults)
    merged.update(parsed)
    return merged


def _resolve_active_profile(
    provider_id: str,
    profiles: Dict[str, ProviderProfile],
) -> ProviderProfile:
    target = profiles.get(provider_id)
    if target is not None:
        return target
    default_profile = profiles.get(DEFAULT_PROVIDER_ID)
    if default_profile is not None:
        return default_profile
    first_profile = next(iter(profiles.values()), None)
    if first_profile is not None:
        return first_profile
    return default_provider_profiles()[DEFAULT_PROVIDER_ID]


def _parse_project_config_data(data: Dict[str, object]) -> ProjectConfig:
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    providers = data.get("providers") if isinstance(data.get("providers"), dict) else {}
    provider_legacy = data.get("provider") if isinstance(data.get("provider"), dict) else {}
    runtime = data.get("runtime") if isinstance(data.get("runtime"), dict) else {}
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    logs = runtime.get("logs") if isinstance(runtime.get("logs"), dict) else {}
    provider_windows = (
        runtime.get("provider_context_windows")
        if isinstance(runtime.get("provider_context_windows"), dict)
        else {}
    )

    windows = dict(DEFAULT_PROVIDER_CONTEXT_WINDOWS)
    for provider_id, default_window in list(windows.items()):
        windows[provider_id] = _safe_positive_int(
            provider_windows.get(provider_id),  # type: ignore[arg-type]
            default_window,
        )
    for key, value in provider_windows.items():  # type: ignore[assignment]
        provider_id = str(key or "").strip().lower()
        if not provider_id:
            continue
        windows[provider_id] = _safe_positive_int(value, windows.get(provider_id, 200000))

    default_provider = _normalize_provider(model.get("default_provider"))  # type: ignore[arg-type]
    if "provider_selected" in model:
        provider_selected = _safe_bool(model.get("provider_selected"), True)  # type: ignore[arg-type]
    else:
        # Legacy config compatibility: older files implicitly had provider selected.
        provider_selected = True
    profiles = _safe_provider_profiles(providers)

    # Legacy compatibility path: parse old [provider] if [providers] missing.
    if not providers and provider_legacy:
        legacy_adapter = (
            provider_legacy.get("adapter")
            if isinstance(provider_legacy.get("adapter"), dict)
            else {}
        )
        legacy_acp = provider_legacy.get("acp") if isinstance(provider_legacy.get("acp"), dict) else {}
        legacy_profile = ProviderProfile(
                provider_id="claude",
                enabled=True,
                backend=_normalize_provider_backend(provider_legacy.get("backend")),  # type: ignore[arg-type]
                adapter_command=str(legacy_adapter.get("command") or DEFAULT_ADAPTER_COMMAND).strip() or DEFAULT_ADAPTER_COMMAND,  # type: ignore[arg-type]
                adapter_args=_safe_string_list(legacy_adapter.get("args"), DEFAULT_ADAPTER_ARGS),  # type: ignore[arg-type]
                adapter_env_allowlist=_safe_string_list(legacy_adapter.get("env_allowlist"), []),  # type: ignore[arg-type]
                acp_connect_timeout_sec=_safe_positive_int(
                    legacy_acp.get("connect_timeout"),  # type: ignore[arg-type]
                    DEFAULT_ACP_CONNECT_TIMEOUT_SEC,
                ),
                acp_request_timeout_sec=_safe_positive_int(
                    legacy_acp.get("request_timeout"),  # type: ignore[arg-type]
                    DEFAULT_ACP_REQUEST_TIMEOUT_SEC,
                ),
                acp_max_retries=_safe_positive_int(
                    legacy_acp.get("max_retries"),  # type: ignore[arg-type]
                    DEFAULT_ACP_MAX_RETRIES,
                ),
                acp_backoff=_safe_backoff(
                    legacy_acp.get("backoff"),  # type: ignore[arg-type]
                    DEFAULT_ACP_BACKOFF,
                ),
                acp_circuit_breaker_enabled=_safe_bool(
                    legacy_acp.get("circuit_breaker_enabled"),  # type: ignore[arg-type]
                    DEFAULT_ACP_CIRCUIT_BREAKER_ENABLED,
                ),
                fallback_enabled=False,
            )
        profiles = default_provider_profiles()
        profiles["claude"] = legacy_profile

    active_profile = _resolve_active_profile(default_provider, profiles)
    return ProjectConfig(
        default_provider=default_provider,
        provider_selected=provider_selected,
        provider_profiles=profiles,
        provider_backend=active_profile.backend,
        provider_adapter_command=active_profile.adapter_command,
        provider_adapter_args=list(active_profile.adapter_args),
        provider_adapter_env_allowlist=list(active_profile.adapter_env_allowlist),
        provider_acp_connect_timeout=active_profile.acp_connect_timeout_sec,
        provider_acp_request_timeout=active_profile.acp_request_timeout_sec,
        provider_acp_max_retries=active_profile.acp_max_retries,
        provider_acp_backoff=active_profile.acp_backoff,
        provider_acp_circuit_breaker_enabled=active_profile.acp_circuit_breaker_enabled,
        default_context_id=str(context.get("default_id") or DEFAULT_CONTEXT_ID),
        max_tool_calls=_safe_positive_int(runtime.get("max_tool_calls"), DEFAULT_MAX_TOOL_CALLS),  # type: ignore[arg-type]
        context_budget_ratio=_safe_ratio(
            runtime.get("context_budget_ratio"),  # type: ignore[arg-type]
            DEFAULT_CONTEXT_BUDGET_RATIO,
        ),
        max_summary_attempts=_safe_positive_int(
            runtime.get("max_summary_attempts"),  # type: ignore[arg-type]
            DEFAULT_MAX_SUMMARY_ATTEMPTS,
        ),
        provider_context_windows=windows,
        logs_enabled=_safe_bool(logs.get("enabled"), DEFAULT_LOGS_ENABLED),  # type: ignore[arg-type]
        logs_format=_safe_log_format(logs.get("format"), DEFAULT_LOGS_FORMAT),  # type: ignore[arg-type]
        logs_max_file_bytes=_safe_positive_int_or_default(
            logs.get("max_file_bytes"),  # type: ignore[arg-type]
            DEFAULT_LOGS_MAX_FILE_BYTES,
        ),
        logs_max_files=_safe_positive_int_or_default(
            logs.get("max_files"),  # type: ignore[arg-type]
            DEFAULT_LOGS_MAX_FILES,
        ),
        logs_redaction=_safe_redaction(logs.get("redaction"), DEFAULT_LOGS_REDACTION),  # type: ignore[arg-type]
    )


def _render_project_config(config: ProjectConfig) -> str:
    def _toml_array(values: Sequence[str]) -> str:
        escaped: List[str] = []
        for item in values:
            text = str(item or "").replace("\\", "\\\\").replace('"', '\\"')
            escaped.append('"{0}"'.format(text))
        return "[{0}]".format(", ".join(escaped))

    profiles = default_provider_profiles()
    profiles.update(config.provider_profiles)

    lines: List[str] = [
        "[model]",
        'default_provider = "{0}"'.format(config.default_provider),
        "provider_selected = {0}".format(str(bool(config.provider_selected)).lower()),
        "",
    ]

    for provider_id in PROFILE_ALLOWED_PROVIDER_IDS:
        profile = profiles[provider_id]
        lines.extend(
            [
                "[providers.{0}]".format(provider_id),
                "enabled = {0}".format(str(bool(profile.enabled)).lower()),
                'backend = "{0}"'.format(_normalize_provider_backend(profile.backend)),
                "",
                "[providers.{0}.adapter]".format(provider_id),
                'command = "{0}"'.format(
                    str(profile.adapter_command).replace("\\", "\\\\").replace('"', '\\"')
                ),
                "args = {0}".format(_toml_array(profile.adapter_args)),
                "env_allowlist = {0}".format(_toml_array(profile.adapter_env_allowlist)),
                "",
                "[providers.{0}.acp]".format(provider_id),
                "connect_timeout = {0}".format(
                    _safe_positive_int(profile.acp_connect_timeout_sec, DEFAULT_ACP_CONNECT_TIMEOUT_SEC)
                ),
                "request_timeout = {0}".format(
                    _safe_positive_int(profile.acp_request_timeout_sec, DEFAULT_ACP_REQUEST_TIMEOUT_SEC)
                ),
                "max_retries = {0}".format(
                    _safe_positive_int(profile.acp_max_retries, DEFAULT_ACP_MAX_RETRIES)
                ),
                'backoff = "{0}"'.format(
                    _safe_backoff(profile.acp_backoff, DEFAULT_ACP_BACKOFF)
                    .replace("\\", "\\\\")
                    .replace('"', '\\"')
                ),
                "circuit_breaker_enabled = {0}".format(
                    str(bool(profile.acp_circuit_breaker_enabled)).lower()
                ),
                "",
                "[providers.{0}.fallback]".format(provider_id),
                "enabled = {0}".format(str(bool(profile.fallback_enabled)).lower()),
                "",
            ]
        )

    lines.extend(
        [
            "[runtime]",
            "max_tool_calls = {0}".format(config.max_tool_calls),
            "context_budget_ratio = {0}".format(config.context_budget_ratio),
            "max_summary_attempts = {0}".format(config.max_summary_attempts),
            "",
            "[runtime.provider_context_windows]",
        ]
    )
    windows = dict(DEFAULT_PROVIDER_CONTEXT_WINDOWS)
    windows.update(config.provider_context_windows)
    for provider_id in sorted(windows.keys()):
        lines.append(
            "{0} = {1}".format(
                provider_id,
                _safe_positive_int(windows.get(provider_id), 200000),
            )
        )
    lines.extend(
        [
            "",
            "[runtime.logs]",
            "enabled = {0}".format(str(bool(config.logs_enabled)).lower()),
            'format = "{0}"'.format(_safe_log_format(config.logs_format, DEFAULT_LOGS_FORMAT)),
            "max_file_bytes = {0}".format(
                _safe_positive_int_or_default(config.logs_max_file_bytes, DEFAULT_LOGS_MAX_FILE_BYTES)
            ),
            "max_files = {0}".format(_safe_positive_int_or_default(config.logs_max_files, DEFAULT_LOGS_MAX_FILES)),
            'redaction = "{0}"'.format(_safe_redaction(config.logs_redaction, DEFAULT_LOGS_REDACTION)),
            "",
            "[context]",
            'default_id = "{0}"'.format(config.default_context_id),
            "",
        ]
    )
    return "\n".join(lines)


def _default_system_prompt() -> str:
    return "\n".join(
        [
            "# Perlica System Prompt",
            "",
            "You are Perlica, a macOS control agent running in a local workspace.",
            "Your job is to complete user tasks safely and efficiently using available tools and integrations.",
            "",
            "Core behavior:",
            "- Prefer concrete actions over abstract advice.",
            "- When tools are available, decide whether to call them based on user intent.",
            "- Use side-effectful tools carefully and explain risky actions before execution.",
            "",
            "Capabilities:",
            "- You can run shell commands through tool calls (e.g. shell.exec).",
            "- You can execute AppleScript when needed for macOS app automation.",
            "- You can leverage Skill context blocks for domain-specific workflows.",
            "- You can use MCP tools/resources/prompts exposed at runtime.",
            "",
            "Output contract:",
            "- Follow the provider tool-call contract strictly.",
            "- Keep assistant text concise and actionable.",
            "- If blocked by permissions or missing tools, state the exact blocker and a fix.",
            "",
        ]
    )


def _default_mcp_servers_config() -> str:
    return "\n".join(
        [
            "# MCP server config for Perlica",
            "# Example:",
            "# [[servers]]",
            "# id = \"filesystem\"",
            "# command = \"npx\"",
            "# args = [\"-y\", \"@modelcontextprotocol/server-filesystem\", \".\"]",
            "# enabled = false",
            "# [servers.env]",
            "# NODE_ENV = \"production\"",
            "",
        ]
    )


def _ensure_runtime_context_artifacts(config_root: Path, context_id: str) -> None:
    context_dir = config_root / "contexts" / context_id
    logs_dir = context_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    for db_name in ("eventlog.db", "approvals.db", "sessions.db"):
        (context_dir / db_name).touch(exist_ok=True)


def initialize_project_config(workspace_dir: Optional[Path] = None, force: bool = False) -> Path:
    project_root = resolve_project_root(workspace_dir)
    config_root = resolve_project_config_root(project_root)
    config_file = config_root / CONFIG_FILE_NAME

    if config_root.exists():
        if not force:
            raise ProjectConfigError(
                "配置目录已存在：{0} (configuration directory already exists)".format(config_root)
            )
        shutil.rmtree(config_root)

    (config_root / "skills").mkdir(parents=True, exist_ok=True)
    (config_root / "plugins").mkdir(parents=True, exist_ok=True)
    (config_root / PROMPTS_DIR_NAME).mkdir(parents=True, exist_ok=True)
    (config_root / MCP_DIR_NAME).mkdir(parents=True, exist_ok=True)

    default_config = ProjectConfig(provider_selected=False)
    default_profile = default_config.provider_profiles.get("claude")
    if default_profile is not None and default_profile.adapter_command in {"python", "python3"}:
        default_config.provider_profiles["claude"] = replace(
            default_profile,
            adapter_command=sys.executable,
        )
        default_config.provider_adapter_command = sys.executable
        default_config.provider_adapter_args = list(default_profile.adapter_args)
    _ensure_runtime_context_artifacts(config_root, default_config.default_context_id)
    config_file.write_text(_render_project_config(default_config), encoding="utf-8")
    (config_root / PROMPTS_DIR_NAME / SYSTEM_PROMPT_FILE_NAME).write_text(
        _default_system_prompt(),
        encoding="utf-8",
    )
    (config_root / MCP_DIR_NAME / MCP_SERVERS_FILE_NAME).write_text(
        _default_mcp_servers_config(),
        encoding="utf-8",
    )
    return config_root


def load_project_config(config_root: Optional[Path] = None, workspace_dir: Optional[Path] = None) -> ProjectConfig:
    resolved_root = (config_root or resolve_project_config_root(workspace_dir)).resolve()
    config_file = resolved_root / CONFIG_FILE_NAME
    if not resolved_root.is_dir() or not config_file.is_file():
        raise ProjectConfigError(
            "缺少项目配置目录：{0}，请先执行 `perlica init` (missing project config directory)".format(
                resolved_root
            )
        )

    try:
        parsed = tomllib.loads(config_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProjectConfigError("配置文件无效：{0} (invalid config file)".format(config_file)) from exc

    if not isinstance(parsed, dict):
        raise ProjectConfigError("配置文件无效：{0} (invalid config file)".format(config_file))

    return _parse_project_config_data(parsed)


def save_project_config(
    config: ProjectConfig,
    config_root: Optional[Path] = None,
    workspace_dir: Optional[Path] = None,
) -> Path:
    resolved_root = (config_root or resolve_project_config_root(workspace_dir)).resolve()
    config_file = resolved_root / CONFIG_FILE_NAME
    if not resolved_root.is_dir():
        raise ProjectConfigError(
            "缺少项目配置目录：{0}，请先执行 `perlica init` (missing project config directory)".format(
                resolved_root
            )
        )
    config_file.write_text(_render_project_config(config), encoding="utf-8")
    return config_file


def get_default_provider(workspace_dir: Optional[Path] = None) -> str:
    config = load_project_config(workspace_dir=workspace_dir)
    return config.default_provider


def set_default_provider(provider_id: str, workspace_dir: Optional[Path] = None) -> str:
    normalized = _normalize_provider(provider_id)
    if normalized != str(provider_id).strip().lower():
        raise ProjectConfigError(
            "不支持的 provider：'{0}'，可选值：{1} (unsupported provider)".format(
                provider_id,
                "|".join(ALLOWED_PROVIDERS),
            )
        )

    config = load_project_config(workspace_dir=workspace_dir)
    config.default_provider = normalized
    config.provider_selected = True
    save_project_config(config, workspace_dir=workspace_dir)
    return normalized


def provider_selection_required(workspace_dir: Optional[Path] = None) -> bool:
    config = load_project_config(workspace_dir=workspace_dir)
    return not bool(config.provider_selected)


def mark_provider_selected(
    provider_id: Optional[str] = None,
    workspace_dir: Optional[Path] = None,
) -> str:
    config = load_project_config(workspace_dir=workspace_dir)
    selected = _normalize_provider(provider_id or config.default_provider)
    if selected not in ALLOWED_PROVIDERS:
        raise ProjectConfigError(
            "不支持的 provider：'{0}'，可选值：{1} (unsupported provider)".format(
                provider_id,
                "|".join(ALLOWED_PROVIDERS),
            )
        )
    config.default_provider = selected
    config.provider_selected = True
    save_project_config(config, workspace_dir=workspace_dir)
    return selected


def load_settings(
    context_id: Optional[str] = None,
    provider: Optional[str] = None,
    max_tool_calls: Optional[int] = None,
    workspace_dir: Optional[Path] = None,
) -> Settings:
    """Resolve settings from project config + explicit overrides."""

    project_root = resolve_project_root(workspace_dir)
    config_root = resolve_project_config_root(project_root)
    project_config = load_project_config(config_root=config_root)

    resolved_context = str(context_id or project_config.default_context_id or DEFAULT_CONTEXT_ID)
    resolved_provider = _normalize_provider(provider or project_config.default_provider)
    resolved_profile = _resolve_active_profile(resolved_provider, project_config.provider_profiles)
    resolved_max_tool_calls = _safe_positive_int(
        max_tool_calls if max_tool_calls is not None else project_config.max_tool_calls,
        DEFAULT_MAX_TOOL_CALLS,
    )

    settings = Settings(
        project_root=project_root,
        config_root=config_root,
        context_id=resolved_context,
        provider=resolved_provider,
        provider_profile=resolved_profile,
        provider_backend=resolved_profile.backend,
        provider_adapter_command=_resolve_adapter_command(
            resolved_profile.adapter_command,
            resolved_profile.adapter_args,
        ),
        provider_adapter_args=list(resolved_profile.adapter_args),
        provider_adapter_env_allowlist=list(resolved_profile.adapter_env_allowlist),
        provider_acp_connect_timeout=resolved_profile.acp_connect_timeout_sec,
        provider_acp_request_timeout=resolved_profile.acp_request_timeout_sec,
        provider_acp_max_retries=resolved_profile.acp_max_retries,
        provider_acp_backoff=resolved_profile.acp_backoff,
        provider_acp_circuit_breaker_enabled=resolved_profile.acp_circuit_breaker_enabled,
        max_tool_calls=resolved_max_tool_calls,
        workspace_dir=project_root,
        provider_context_windows=dict(project_config.provider_context_windows),
        context_budget_ratio=project_config.context_budget_ratio,
        max_summary_attempts=project_config.max_summary_attempts,
        logs_enabled=project_config.logs_enabled,
        logs_format=project_config.logs_format,
        logs_max_file_bytes=project_config.logs_max_file_bytes,
        logs_max_files=project_config.logs_max_files,
        logs_redaction=project_config.logs_redaction,
    )

    # Context-level storage is lazily created when a context is first used.
    _ensure_runtime_context_artifacts(settings.config_root, settings.context_id)

    settings.plugin_dirs = _discover_plugin_dirs(settings)
    settings.skill_dirs = _discover_skill_dirs(settings)
    return settings
