"""Interactive chat entrypoint: TTY uses TUI, non-TTY reads stdin once."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional, TextIO

from perlica.config import (
    ALLOWED_PROVIDERS,
    ProjectConfigError,
    initialize_project_config,
    load_settings,
    project_config_exists,
)
from perlica.tui.controller import start_tui_chat
from perlica.tui.service_controller import start_tui_service
from perlica.security.permission_probe import run_startup_permission_checks
from perlica.ui.render import render_notice

RunExecutor = Callable[[str, Optional[str], bool, Optional[str], Optional[str]], int]


def start_repl(
    *,
    provider: Optional[str],
    yes: bool,
    context_id: Optional[str],
    run_executor: RunExecutor,
    stream: TextIO = sys.stdout,
    err_stream: TextIO = sys.stderr,
) -> int:
    validated_provider = _validate_provider(provider=provider, stream=err_stream)
    if provider is not None and validated_provider is None:
        return 2

    if not project_config_exists():
        try:
            config_root = initialize_project_config(force=False)
        except ProjectConfigError as exc:
            _echo(err_stream, render_notice("error", str(exc)))
            return 2
        _echo(
            stream,
            render_notice(
                "info",
                "未检测到项目配置，已自动初始化：{0}".format(config_root),
                "Project config was missing and has been auto-initialized.",
            ),
        )

    settings = load_settings(context_id=context_id, provider=validated_provider)
    resolved_provider = settings.provider

    if not _stdin_is_tty():
        stdin_text = sys.stdin.read().strip()
        if not stdin_text:
            _echo(
                err_stream,
                render_notice(
                    "error",
                    "未检测到输入内容，请使用 `perlica run \"...\"` 或通过 stdin 传入文本。",
                    "No stdin payload. Use `perlica run \"...\"` or pipe content to stdin.",
                ),
            )
            return 2
        return run_executor(
            stdin_text,
            resolved_provider,
            yes,
            context_id,
            None,
        )

    _emit_permission_probe_messages(
        run_startup_permission_checks(
            workspace_dir=Path.cwd(),
            trigger_applescript=True,
        ),
        stream=stream,
    )

    try:
        return start_tui_chat(provider=resolved_provider, yes=yes, context_id=context_id)
    except RuntimeError as exc:
        _echo(err_stream, render_notice("error", str(exc)))
        return 1


def start_service_mode(
    *,
    provider: Optional[str],
    yes: bool,
    context_id: Optional[str],
    stream: TextIO = sys.stdout,
    err_stream: TextIO = sys.stderr,
) -> int:
    validated_provider = _validate_provider(provider=provider, stream=err_stream)
    if provider is not None and validated_provider is None:
        return 2

    if not project_config_exists():
        try:
            config_root = initialize_project_config(force=False)
        except ProjectConfigError as exc:
            _echo(err_stream, render_notice("error", str(exc)))
            return 2
        _echo(
            stream,
            render_notice(
                "info",
                "未检测到项目配置，已自动初始化：{0}".format(config_root),
                "Project config was missing and has been auto-initialized.",
            ),
        )

    settings = load_settings(context_id=context_id, provider=validated_provider)
    resolved_provider = settings.provider

    if not _stdin_is_tty():
        _echo(
            err_stream,
            render_notice(
                "error",
                "`--service` 需要在交互终端启动（TTY）。",
                "`--service` requires an interactive TTY terminal.",
            ),
        )
        return 2

    _emit_permission_probe_messages(
        run_startup_permission_checks(
            workspace_dir=Path.cwd(),
            trigger_applescript=True,
        ),
        stream=stream,
    )

    try:
        return start_tui_service(provider=resolved_provider, yes=yes, context_id=context_id)
    except RuntimeError as exc:
        _echo(err_stream, render_notice("error", str(exc)))
        return 1


def _stdin_is_tty() -> bool:
    isatty = getattr(sys.stdin, "isatty", None)
    if callable(isatty):
        try:
            return bool(isatty())
        except Exception:
            return False
    return False


def _echo(stream: TextIO, text: str) -> None:
    stream.write(text + "\n")
    stream.flush()


def _validate_provider(provider: Optional[str], stream: TextIO) -> Optional[str]:
    normalized = str(provider or "").strip().lower()
    if not normalized:
        return None
    if normalized not in ALLOWED_PROVIDERS:
        _echo(
            stream,
            render_notice(
                "error",
                "不支持的 provider：{0}，当前仅支持：claude。".format(provider),
                "Unsupported provider: {0}. Supported: claude.".format(provider),
            ),
        )
        return None
    return normalized


def _emit_permission_probe_messages(report: dict, stream: TextIO) -> None:
    checks = report.get("checks")
    if not isinstance(checks, dict):
        return
    for key in ("shell", "applescript"):
        item = checks.get(key)
        if not isinstance(item, dict):
            continue
        if bool(item.get("ok")):
            continue
        detail = str(item.get("detail") or "")
        hint = str(item.get("hint") or "")
        message = "启动权限检查未通过：{0} - {1}".format(key, detail)
        if hint:
            message = "{0}；{1}".format(message, hint)
        _echo(
            stream,
            render_notice(
                "warn",
                message,
                "Startup permission check failed: {0}".format(key),
            ),
        )
