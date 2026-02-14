"""Presentation helpers for Perlica CLI output."""

from __future__ import annotations

import io
from typing import Any, Dict, Iterable, Optional, TextIO

try:  # pragma: no cover - optional at runtime
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    _HAS_RICH = True
except Exception:  # pragma: no cover - fallback when Rich is unavailable
    _HAS_RICH = False
    Console = None  # type: ignore[assignment]
    Panel = None  # type: ignore[assignment]
    Text = None  # type: ignore[assignment]
    box = None  # type: ignore[assignment]


def bilingual_text(zh: str, en: Optional[str] = None) -> str:
    if not en:
        return zh
    return "{0} ({1})".format(zh, en)


def render_notice(level: str, zh: str, en: Optional[str] = None) -> str:
    prefix_map = {
        "info": bilingual_text("提示", "Info"),
        "warn": bilingual_text("警告", "Warning"),
        "error": bilingual_text("错误", "Error"),
        "success": bilingual_text("成功", "Success"),
    }
    prefix = prefix_map.get(level, bilingual_text("提示", "Info"))
    return "{0}: {1}".format(prefix, bilingual_text(zh, en))


def _is_tty(stream: TextIO, forced: Optional[bool]) -> bool:
    if forced is not None:
        return forced
    isatty = getattr(stream, "isatty", None)
    if callable(isatty):
        try:
            return bool(isatty())
        except Exception:
            return False
    return False


def render_assistant_panel(
    text: str,
    stream: TextIO,
    is_tty: Optional[bool] = None,
) -> None:
    tty = _is_tty(stream, is_tty)
    normalized = text if text is not None else ""

    if tty and _HAS_RICH:
        console = Console(file=stream, highlight=False, soft_wrap=True)
        console.print(
            Panel(
                normalized,
                title=bilingual_text("助手回复", "Assistant"),
                border_style="cyan",
                box=box.ROUNDED,
            )
        )
        return

    title = bilingual_text("助手回复", "Assistant")
    lines = normalized.splitlines() or [""]
    width = max([len(title)] + [len(line) for line in lines])

    top = "+-{0}-+".format(title.ljust(width, "-"))
    stream.write(top + "\n")
    for line in lines:
        stream.write("| {0} |\n".format(line.ljust(width)))
    stream.write("+-{0}-+\n".format("-" * width))
    stream.flush()


def _meta_lines(result: object) -> Iterable[str]:
    context_usage: Dict[str, Any] = dict(getattr(result, "context_usage", {}) or {})
    llm_calls = list(getattr(result, "llm_call_usages", []) or [])
    total_usage = getattr(result, "total_usage", None)

    yield bilingual_text("会话信息", "Session")
    yield "id={0} name={1} provider_locked={2}".format(
        getattr(result, "session_id", ""),
        getattr(result, "session_name", "") or "",
        getattr(result, "provider_id", ""),
    )

    yield bilingual_text("上下文使用", "Context Usage")
    yield (
        "history_messages_included={0} summary_versions_used={1} estimated_context_tokens={2}".format(
            int(context_usage.get("history_messages_included") or 0),
            int(context_usage.get("summary_versions_used") or 0),
            int(context_usage.get("estimated_context_tokens") or 0),
        )
    )

    yield bilingual_text("Token 总计", "Token Usage Total")
    yield "input_tokens={0} cached_input_tokens={1} output_tokens={2}".format(
        int(getattr(total_usage, "input_tokens", 0)),
        int(getattr(total_usage, "cached_input_tokens", 0)),
        int(getattr(total_usage, "output_tokens", 0)),
    )

    yield bilingual_text("Token 分调用", "Token Usage By Call")
    if not llm_calls:
        yield bilingual_text("无", "none")
        return

    for call in llm_calls:
        yield "call={0} provider={1} input={2} cached={3} output={4}".format(
            getattr(call, "call_index", 0),
            getattr(call, "provider_id", ""),
            int(getattr(call, "input_tokens", 0)),
            int(getattr(call, "cached_input_tokens", 0)),
            int(getattr(call, "output_tokens", 0)),
        )


def render_run_meta(result: object, stream: TextIO, is_tty: Optional[bool] = None) -> None:
    tty = _is_tty(stream, is_tty)
    lines = list(_meta_lines(result))

    if tty and _HAS_RICH:
        console = Console(file=stream, highlight=False, soft_wrap=True)
        for line in lines:
            console.print(Text(line, style="dim"))
        return

    for line in lines:
        stream.write(line + "\n")
    stream.flush()


def render_doctor_text(report: Dict[str, Any]) -> str:
    providers = report.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    provider_lines = []
    for provider_id in sorted(str(key) for key in providers.keys()):
        provider_lines.append(
            "{0}={1}".format(
                provider_id,
                "ok" if providers.get(provider_id) else "missing",
            )
        )
    if not provider_lines:
        provider_lines = ["claude=missing", "opencode=missing"]

    lines = [
        bilingual_text("系统诊断", "Doctor Report"),
        "context_id={0}".format(report.get("context_id", "")),
        "context_dir={0}".format(report.get("context_dir", "")),
        "",
        bilingual_text("Provider 可用性", "Provider Availability"),
        *provider_lines,
        "active_provider={0}".format(str(report.get("active_provider") or "")),
        "adapter_probe={0}".format(str(report.get("provider_adapter_probe") or "")),
        "",
        bilingual_text("运行状态", "Runtime Health"),
        "db_writable={0}".format(bool(report.get("db_writable"))),
        "plugins_loaded={0} plugins_failed={1}".format(
            int(report.get("plugins_loaded") or 0),
            int(report.get("plugins_failed") or 0),
        ),
        "skills_loaded={0} skills_errors={1}".format(
            int(report.get("skills_loaded") or 0),
            int(report.get("skills_errors") or 0),
        ),
        "system_prompt_loaded={0}".format(bool(report.get("system_prompt_loaded"))),
        "skill_prompt_injection_enabled={0}".format(bool(report.get("skill_prompt_injection_enabled", True))),
        "provider_backend={0}".format(str(report.get("provider_backend") or "")),
        "acp_adapter_status={0} acp_session_errors={1}".format(
            str(report.get("acp_adapter_status") or ""),
            int(report.get("acp_session_errors") or 0),
        ),
        "",
        bilingual_text("调试日志", "Debug Logs"),
        "logs_enabled={0}".format(bool(report.get("logs_enabled"))),
        "logs_active_size_bytes={0} logs_total_size_bytes={1}".format(
            int(report.get("logs_active_size_bytes") or 0),
            int(report.get("logs_total_size_bytes") or 0),
        ),
        "logs_max_file_bytes={0} logs_max_files={1}".format(
            int(report.get("logs_max_file_bytes") or 0),
            int(report.get("logs_max_files") or 0),
        ),
        "logs_write_errors={0}".format(int(report.get("logs_write_errors") or 0)),
        "",
        bilingual_text("MCP 状态", "MCP Status"),
        "mcp_servers_loaded={0} mcp_tools_loaded={1}".format(
            int(report.get("mcp_servers_loaded") or 0),
            int(report.get("mcp_tools_loaded") or 0),
        ),
    ]

    logs_dir = report.get("logs_dir")
    if logs_dir:
        lines.append("logs_dir={0}".format(logs_dir))
    logs_active_file = report.get("logs_active_file")
    if logs_active_file:
        lines.append("logs_active_file={0}".format(logs_active_file))
    rotated = report.get("logs_rotated_files")
    if isinstance(rotated, list):
        lines.append("logs_rotated_files={0}".format(len(rotated)))

    permissions = report.get("permissions")
    if isinstance(permissions, dict) and permissions:
        lines.append("")
        lines.append(bilingual_text("权限检查", "Permission Checks"))
        for key in ("shell", "applescript"):
            item = permissions.get(key)
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "unknown")
            detail = str(item.get("detail") or "")
            lines.append("{0}: {1} ({2})".format(key, status, detail))

    mcp_errors = report.get("mcp_errors")
    if isinstance(mcp_errors, dict) and mcp_errors:
        lines.append("")
        lines.append(bilingual_text("MCP 错误", "MCP Errors"))
        for key in sorted(mcp_errors.keys()):
            lines.append("{0}: {1}".format(key, mcp_errors[key]))

    plugin_failures = report.get("plugin_failures")
    if isinstance(plugin_failures, dict) and plugin_failures:
        lines.append("")
        lines.append(bilingual_text("插件失败详情", "Plugin Failures"))
        for plugin_id in sorted(plugin_failures.keys()):
            lines.append("{0}: {1}".format(plugin_id, plugin_failures[plugin_id]))

    skill_errors = report.get("skill_errors")
    if isinstance(skill_errors, dict) and skill_errors:
        lines.append("")
        lines.append(bilingual_text("技能加载错误", "Skill Errors"))
        for skill_id in sorted(skill_errors.keys()):
            lines.append("{0}: {1}".format(skill_id, skill_errors[skill_id]))

    db_error = report.get("db_error")
    if db_error:
        lines.append("")
        lines.append(bilingual_text("数据库写入错误", "Database Error"))
        lines.append(str(db_error))

    mcp_servers = report.get("mcp_servers")
    if isinstance(mcp_servers, list) and mcp_servers:
        lines.append("")
        lines.append(bilingual_text("MCP Server 详情", "MCP Server Details"))
        for row in mcp_servers:
            if not isinstance(row, dict):
                continue
            lines.append(
                "{0} loaded={1} tools={2} resources={3} prompts={4} error={5}".format(
                    row.get("server_id", ""),
                    row.get("loaded", ""),
                    row.get("tool_count", 0),
                    row.get("resource_count", 0),
                    row.get("prompt_count", 0),
                    row.get("error") or "",
                )
            )

    return "\n".join(lines)


def preview_rendered_run_meta(result: object) -> str:
    """Helper for tests that need a deterministic text snapshot."""

    stream = io.StringIO()
    render_run_meta(result=result, stream=stream, is_tty=False)
    return stream.getvalue()


def render_repl_banner(
    *,
    context_id: str,
    session_id: str,
    session_name: str,
    provider_id: str,
) -> str:
    lines = [
        bilingual_text("Perlica 交互会话已启动", "Perlica REPL started"),
        bilingual_text("输入自然语言即可对话", "Type natural language to chat"),
        bilingual_text("输入 /help 查看命令", "Type /help for commands"),
        "context={0} session={1} name={2} provider={3}".format(
            context_id,
            session_id,
            session_name,
            provider_id,
        ),
    ]
    return "\n".join(lines)


def render_repl_help_summary() -> str:
    lines = [
        bilingual_text("可用命令", "Available commands"),
        "/help",
        "/clear",
        "/pending",
        "/choose <index|text...>",
        "/exit | /quit",
        "/save [name]",
        "/discard",
        "/session [list [--all]|new [--name NAME] [--provider <provider_id>]|use <ref>|current]",
        "/doctor [--format json|text] [--verbose]",
        "/mcp [list|reload|status]",
        "/skill [list|reload]",
        "/policy approvals [list|reset --all|reset --tool <name> --risk <tier>]",
        "/service [status|rebind|unpair|channel list|channel use <id>|channel current|tools list|tools allow|tools deny]",
    ]
    return "\n".join(lines)
