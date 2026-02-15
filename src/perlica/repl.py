"""Interactive chat entrypoint: TTY uses TUI, non-TTY reads stdin once."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional, TextIO

from perlica.config import (
    ALLOWED_PROVIDERS,
    ProjectConfigError,
    initialize_project_config,
    load_project_config,
    load_settings,
    mark_provider_selected,
    project_config_exists,
)
from perlica.providers.static_sync.manager import (
    format_static_sync_report_lines,
    static_sync_notice,
    sync_provider_static_config,
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

    resolved_provider, selection_code = _resolve_provider_with_first_selection(
        provider=validated_provider,
        stdin_tty=_stdin_is_tty(),
        stream=stream,
        err_stream=err_stream,
    )
    if selection_code != 0:
        return selection_code

    settings = load_settings(context_id=context_id, provider=resolved_provider)
    resolved_provider = settings.provider

    stdin_tty = _stdin_is_tty()
    if not stdin_tty:
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

    static_sync_report = sync_provider_static_config(
        settings=settings,
        provider_id=resolved_provider,
    )
    _emit_static_sync_messages(report=static_sync_report, stream=stream, err_stream=err_stream)

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

    resolved_provider, selection_code = _resolve_provider_with_first_selection(
        provider=validated_provider,
        stdin_tty=_stdin_is_tty(),
        stream=stream,
        err_stream=err_stream,
    )
    if selection_code != 0:
        return selection_code

    settings = load_settings(context_id=context_id, provider=resolved_provider)
    resolved_provider = settings.provider

    stdin_tty = _stdin_is_tty()
    if not stdin_tty:
        _echo(
            err_stream,
            render_notice(
                "error",
                "`--service` 需要在交互终端启动（TTY）。",
                "`--service` requires an interactive TTY terminal.",
            ),
        )
        return 2

    static_sync_report = sync_provider_static_config(
        settings=settings,
        provider_id=resolved_provider,
    )
    _emit_static_sync_messages(report=static_sync_report, stream=stream, err_stream=err_stream)

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
                "不支持的 provider：{0}，当前支持：{1}。".format(
                    provider,
                    "|".join(ALLOWED_PROVIDERS),
                ),
                "Unsupported provider: {0}. Supported: {1}.".format(
                    provider,
                    "|".join(ALLOWED_PROVIDERS),
                ),
            ),
        )
        return None
    return normalized


def _resolve_provider_with_first_selection(
    *,
    provider: Optional[str],
    stdin_tty: bool,
    stream: TextIO,
    err_stream: TextIO,
) -> tuple[Optional[str], int]:
    try:
        project_config = load_project_config()
    except ProjectConfigError as exc:
        _echo(err_stream, render_notice("error", str(exc)))
        return None, 2

    if project_config.provider_selected:
        return provider, 0

    if provider:
        selected = mark_provider_selected(provider)
        _echo(
            stream,
            render_notice(
                "success",
                "首次启动已选择 provider：{0}".format(selected),
                "Selected provider for first launch: {0}".format(selected),
            ),
        )
        return selected, 0

    if not stdin_tty:
        _echo(
            err_stream,
            render_notice(
                "error",
                "首次非交互运行必须显式指定 `--provider {0}`。".format("|".join(ALLOWED_PROVIDERS)),
                "First non-interactive run requires `--provider {0}`.".format(
                    "|".join(ALLOWED_PROVIDERS)
                ),
            ),
        )
        return None, 2

    selected = _prompt_first_provider_selection(stream=stream, err_stream=err_stream)
    if not selected:
        _echo(
            err_stream,
            render_notice(
                "error",
                "未完成 provider 选择，已取消启动。",
                "Provider selection cancelled.",
            ),
        )
        return None, 2
    persisted = mark_provider_selected(selected)
    _echo(
        stream,
        render_notice(
            "success",
            "已保存默认 provider：{0}".format(persisted),
            "Default provider saved: {0}".format(persisted),
        ),
    )
    return persisted, 0


def _prompt_first_provider_selection(stream: TextIO, err_stream: TextIO) -> Optional[str]:
    choices = list(ALLOWED_PROVIDERS)
    _echo(
        stream,
        render_notice(
            "info",
            "首次启动请选择 provider（只会询问一次）。",
            "First launch: choose provider (one-time).",
        ),
    )
    for index, provider_id in enumerate(choices, start=1):
        _echo(stream, "{0}) {1}".format(index, provider_id))

    while True:
        _echo(stream, "请输入编号或 provider id (index/provider):")
        answer = sys.stdin.readline()
        if answer == "":
            return None
        text = answer.strip().lower()
        if text in ALLOWED_PROVIDERS:
            return text
        if text.isdigit():
            index = int(text)
            if 1 <= index <= len(choices):
                return choices[index - 1]
        _echo(
            err_stream,
            render_notice(
                "warn",
                "无效选择，请输入编号或 {0}。".format("|".join(ALLOWED_PROVIDERS)),
                "Invalid selection. Use index or {0}.".format("|".join(ALLOWED_PROVIDERS)),
            ),
        )


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


def _emit_static_sync_messages(*, report: object, stream: TextIO, err_stream: TextIO) -> None:
    level, zh_text, en_text, has_failures = static_sync_notice(report)
    target_stream = err_stream if has_failures else stream
    _echo(target_stream, render_notice(level, zh_text, en_text))

    for line in format_static_sync_report_lines(report):
        _echo(target_stream, "  {0}".format(line))
