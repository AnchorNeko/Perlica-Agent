"""Typer CLI entrypoints for Perlica."""

from __future__ import annotations

import json
import sys
from typing import Callable, Dict, List, Optional

import click
import typer
from typer.core import TyperGroup

from perlica.config import (
    ALLOWED_PROVIDERS,
    ProjectConfigError,
    initialize_project_config,
    load_project_config,
    load_settings,
    mark_provider_selected,
    project_config_exists,
    resolve_project_config_root,
)
from perlica.kernel.loading import LoadingReporter
from perlica.kernel.policy_engine import (
    APPROVAL_ALWAYS_ALLOW,
    APPROVAL_ALWAYS_ASK,
    APPROVAL_ALWAYS_DENY,
    ApprovalAction,
)
from perlica.kernel.runner import Runner
from perlica.kernel.runtime import Runtime
from perlica.kernel.session_store import SessionRecord, SessionStore
from perlica.kernel.types import ToolCall
from perlica.prompt.system_prompt import PromptLoadError
from perlica.providers.base import ProviderError, provider_error_summary
from perlica.repl import start_repl, start_service_mode
from perlica.security.permission_probe import run_startup_permission_checks
from perlica.ui.render import (
    render_assistant_panel,
    render_doctor_text,
    render_notice,
    render_run_meta,
)


class PerlicaGroup(TyperGroup):
    """Treat unknown first positional token as implicit `run` command."""

    def resolve_command(self, ctx: click.Context, args: List[str]):
        if args and not args[0].startswith("-"):
            known = set(self.list_commands(ctx))
            if args[0] in {"model"}:
                return super().resolve_command(ctx, args)
            if args[0] not in known:
                run_command = self.get_command(ctx, "run")
                if run_command is not None:
                    return "run", run_command, args
        return super().resolve_command(ctx, args)


app = typer.Typer(
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Perlica 命令行 Agent (CLI agent)",
)
app.info.cls = PerlicaGroup

skill_app = typer.Typer(help="技能管理 (Skill management)")
policy_app = typer.Typer(help="策略管理 (Policy management)")
approvals_app = typer.Typer(help="审批偏好管理 (Approval preferences)")
session_app = typer.Typer(help="会话管理 (Session management)")
policy_app.add_typer(approvals_app, name="approvals")
app.add_typer(skill_app, name="skill")
app.add_typer(policy_app, name="policy")
app.add_typer(session_app, name="session")


def _missing_config_message() -> str:
    return render_notice(
        "error",
        "当前目录缺少项目配置目录：{0}，请先执行 `perlica init`。".format(
            resolve_project_config_root()
        ),
        "Missing project config directory. Run `perlica init` first.",
    )


def _require_project_config() -> None:
    if project_config_exists():
        return
    typer.echo(_missing_config_message(), err=True)
    raise typer.Exit(code=2)


def _build_approval_resolver(yes: bool) -> Callable[[ToolCall, str], ApprovalAction]:
    if yes:
        return lambda _call, _risk: ApprovalAction(allow=True, reason="cli_yes")

    def resolver(call: ToolCall, risk_tier: str) -> ApprovalAction:
        if not sys.stdin.isatty():
            return ApprovalAction(allow=False, reason="approval_required_non_tty")

        cmd = str(call.arguments.get("cmd") or "")
        typer.echo(render_notice("warn", "工具执行需要确认。", "Tool execution requires approval."))
        typer.echo("tool={0} risk={1}".format(call.tool_name, risk_tier))
        if cmd:
            typer.echo("cmd={0}".format(cmd))
        typer.echo("1) 允许一次 (Allow once)")
        typer.echo("2) 永久允许 (Always allow)")
        typer.echo("3) 拒绝一次 (Deny once)")
        typer.echo("4) 永久拒绝 (Always deny)")
        typer.echo("5) 始终询问（本次允许） (Always ask, allow this time)")
        choice = typer.prompt("请选择 [1-5] (Choose [1-5])", default="1").strip()

        if choice == "2":
            return ApprovalAction(
                allow=True,
                persist_policy=APPROVAL_ALWAYS_ALLOW,
                reason="user_always_allow",
            )
        if choice == "3":
            return ApprovalAction(allow=False, reason="user_deny_once")
        if choice == "4":
            return ApprovalAction(
                allow=False,
                persist_policy=APPROVAL_ALWAYS_DENY,
                reason="user_always_deny",
            )
        if choice == "5":
            return ApprovalAction(
                allow=True,
                persist_policy=APPROVAL_ALWAYS_ASK,
                reason="user_keep_ask",
            )

        return ApprovalAction(allow=True, reason="user_allow_once")

    return resolver


def _validate_provider(provider: Optional[str]) -> Optional[str]:
    normalized = str(provider or "").strip().lower()
    if not normalized:
        return None
    if normalized not in ALLOWED_PROVIDERS:
        typer.echo(
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
            err=True,
        )
        return None
    return normalized


def _prompt_first_provider_selection() -> str:
    choices = list(ALLOWED_PROVIDERS)
    typer.echo(
        render_notice(
            "info",
            "首次启动请选择 provider（只会询问一次）：",
            "First launch: choose provider (one-time).",
        )
    )
    for index, provider_id in enumerate(choices, start=1):
        typer.echo("{0}) {1}".format(index, provider_id))

    while True:
        answer = typer.prompt(
            "请输入编号或 provider id (Enter index or provider id)",
            default="1",
        ).strip().lower()
        if answer in ALLOWED_PROVIDERS:
            return answer
        if answer.isdigit():
            number = int(answer)
            if 1 <= number <= len(choices):
                return choices[number - 1]
        typer.echo(
            render_notice(
                "warn",
                "无效选择，请输入编号或 {0}。".format("|".join(ALLOWED_PROVIDERS)),
                "Invalid selection. Use index or {0}.".format("|".join(ALLOWED_PROVIDERS)),
            ),
            err=True,
        )


def _resolve_provider_with_first_selection(provider: Optional[str]) -> tuple[Optional[str], int]:
    validated_provider = _validate_provider(provider)
    if provider is not None and validated_provider is None:
        return None, 2

    try:
        project_config = load_project_config()
    except ProjectConfigError as exc:
        typer.echo(render_notice("error", str(exc)), err=True)
        return None, 2

    if project_config.provider_selected:
        return validated_provider, 0

    if validated_provider:
        selected = mark_provider_selected(validated_provider)
        typer.echo(
            render_notice(
                "success",
                "首次启动已选择 provider：{0}".format(selected),
                "Selected provider for first launch: {0}".format(selected),
            )
        )
        return selected, 0

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        typer.echo(
            render_notice(
                "error",
                "首次非交互运行必须显式指定 `--provider {0}`。".format("|".join(ALLOWED_PROVIDERS)),
                "First non-interactive run requires `--provider {0}`.".format(
                    "|".join(ALLOWED_PROVIDERS)
                ),
            ),
            err=True,
        )
        return None, 2

    selected = _prompt_first_provider_selection()
    selected = mark_provider_selected(selected)
    typer.echo(
        render_notice(
            "success",
            "已保存默认 provider：{0}".format(selected),
            "Default provider saved: {0}".format(selected),
        )
    )
    return selected, 0


def _execute_prompt(
    text: str,
    provider: Optional[str],
    yes: bool,
    context_id: Optional[str],
    session_ref: Optional[str],
) -> int:
    validated_provider, code = _resolve_provider_with_first_selection(provider)
    if code != 0:
        return code

    settings = load_settings(context_id=context_id, provider=validated_provider)
    resolved_provider = settings.provider
    permission_report = run_startup_permission_checks(
        workspace_dir=settings.workspace_dir,
        trigger_applescript=True,
    )
    _emit_permission_warnings(permission_report)
    try:
        runtime = Runtime(settings)
    except PromptLoadError as exc:
        typer.echo(render_notice("error", str(exc)), err=True)
        return 2
    reporter = LoadingReporter(stream=sys.stderr)

    try:
        initial_session = session_ref or "auto"
        reporter.start(
            context_id=settings.context_id,
            session_id=initial_session,
            provider_id=resolved_provider,
        )

        def on_progress(stage: str, payload: Dict[str, str]) -> None:
            reporter.update(
                stage=stage,
                context_id=payload.get("context_id") or settings.context_id,
                session_id=payload.get("session_id") or initial_session,
                provider_id=payload.get("provider_id") or resolved_provider,
                detail=payload.get("detail") or "",
            )

        runner = Runner(
            runtime=runtime,
            provider_id=resolved_provider,
            max_tool_calls=settings.max_tool_calls,
            approval_resolver=_build_approval_resolver(yes),
        )
        result = runner.run_text(
            text=text,
            assume_yes=yes,
            session_ref=session_ref,
            progress_callback=on_progress,
        )
        reporter.stop()

        render_assistant_panel(result.assistant_text, stream=sys.stdout)
        render_run_meta(result, stream=sys.stdout)
        return 0
    except ProviderError as exc:
        reporter.stop()
        error_detail = provider_error_summary(exc)
        typer.echo(
            render_notice(
                "error",
                "模型调用失败：{0}".format(error_detail),
                "Provider error: {0}".format(error_detail),
            ),
            err=True,
        )
        typer.echo(
            render_notice(
                "info",
                "可先执行 `perlica doctor --format text` 检查 ACP 适配器与权限。",
                "Run `perlica doctor --format text` to diagnose adapter/permissions.",
            ),
            err=True,
        )
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        reporter.stop()
        typer.echo(
            render_notice("error", "执行失败：{0}".format(exc), "Execution failed: {0}".format(exc)),
            err=True,
        )
        return 1
    finally:
        runtime.close()


def _format_session_line(session: SessionRecord, current_id: Optional[str], include_context: bool) -> str:
    marker = "*" if current_id and current_id == session.session_id else " "
    context_part = " context={0}".format(session.context_id) if include_context else ""
    ephemeral_part = " ephemeral={0}".format("yes" if session.is_ephemeral else "no")
    return "{0} {1} name={2} provider={3}{4}{5}".format(
        marker,
        session.session_id,
        session.name or "",
        session.provider_locked or "",
        context_part,
        ephemeral_part,
    )


def _emit_permission_warnings(report: Dict[str, object]) -> None:
    checks = report.get("checks")
    if not isinstance(checks, dict):
        return
    for name in ("shell", "applescript"):
        item = checks.get(name)
        if not isinstance(item, dict):
            continue
        if bool(item.get("ok")):
            continue
        detail = str(item.get("detail") or "")
        hint = str(item.get("hint") or "")
        message = "启动权限检查未通过：{0} - {1}".format(name, detail)
        if hint:
            message = "{0}；{1}".format(message, hint)
        typer.echo(
            render_notice(
                "warn",
                message,
                "Startup permission check failed: {0}".format(name),
            ),
            err=True,
        )


def _execute_chat(
    provider: Optional[str],
    yes: bool,
    context_id: Optional[str],
) -> int:
    validated_provider, code = _resolve_provider_with_first_selection(provider)
    if code != 0:
        return code
    settings = load_settings(context_id=context_id, provider=validated_provider)
    return start_repl(
        provider=settings.provider,
        yes=yes,
        context_id=context_id,
        run_executor=_execute_prompt,
    )


def _execute_service(
    provider: Optional[str],
    yes: bool,
    context_id: Optional[str],
) -> int:
    validated_provider, code = _resolve_provider_with_first_selection(provider)
    if code != 0:
        return code
    settings = load_settings(context_id=context_id, provider=validated_provider)
    return start_service_mode(
        provider=settings.provider,
        yes=yes,
        context_id=context_id,
    )


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        help="模型提供方（默认取配置） (Provider ID, default from config)",
    ),
    yes: bool = typer.Option(False, "--yes", help="仅本次跳过审批确认 (Skip approval once)"),
    context_id: Optional[str] = typer.Option(None, "--context", help="上下文 ID (Perlica context ID)"),
    service: bool = typer.Option(False, "--service", help="前台服务模式（手机桥接） (Foreground bridge mode)"),
) -> None:
    ctx.obj = ctx.obj or {}
    ctx.obj["provider"] = provider
    ctx.obj["yes"] = yes
    ctx.obj["context_id"] = context_id

    if ctx.invoked_subcommand is not None:
        if ctx.invoked_subcommand not in {"init", "chat"}:
            _require_project_config()
        return

    if service:
        exit_code = _execute_service(
            provider=provider,
            yes=yes,
            context_id=context_id,
        )
    else:
        exit_code = _execute_chat(
            provider=provider,
            yes=yes,
            context_id=context_id,
        )
    raise typer.Exit(code=exit_code)


@app.command("init")
def init_cmd(
    force: bool = typer.Option(
        False,
        "--force",
        help="重建 .perlica_config（会先删除已有目录） (Recreate config directory)",
    ),
) -> None:
    try:
        config_root = initialize_project_config(force=force)
    except ProjectConfigError as exc:
        typer.echo(render_notice("error", str(exc)), err=True)
        raise typer.Exit(code=2)

    typer.echo(
        render_notice(
            "success",
            "项目配置初始化完成：{0}".format(config_root),
            "Initialized project config at: {0}".format(config_root),
        )
    )


@app.command("run")
def run_cmd(
    ctx: typer.Context,
    text_parts: List[str] = typer.Argument(..., help="自然语言指令 (Natural language instruction)"),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        help="模型提供方（默认取配置） (Provider ID, default from config)",
    ),
    yes: bool = typer.Option(False, "--yes", help="仅本次跳过审批确认 (Skip approval once)"),
    context_id: Optional[str] = typer.Option(None, "--context", help="上下文 ID (Perlica context ID)"),
    session_ref: Optional[str] = typer.Option(None, "--session", help="会话 ID/名称/前缀 (Session ref)"),
) -> None:
    """执行一轮 Perlica 对话 (Run one Perlica turn)."""
    text = " ".join(text_parts).strip()
    if not text:
        typer.echo(render_notice("error", "请输入指令文本。", "Prompt text is required."), err=True)
        raise typer.Exit(code=2)

    parent_obj = ctx.obj or {}
    resolved_provider = provider if provider is not None else parent_obj.get("provider")
    resolved_yes = yes or bool(parent_obj.get("yes"))
    resolved_context = context_id if context_id is not None else parent_obj.get("context_id")

    exit_code = _execute_prompt(
        text=text,
        provider=resolved_provider,
        yes=resolved_yes,
        context_id=resolved_context,
        session_ref=session_ref,
    )
    raise typer.Exit(code=exit_code)


@app.command("chat")
def chat_cmd(
    ctx: typer.Context,
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        help="模型提供方（默认取配置） (Provider ID, default from config)",
    ),
    yes: bool = typer.Option(False, "--yes", help="仅本次跳过审批确认 (Skip approval once)"),
    context_id: Optional[str] = typer.Option(None, "--context", help="上下文 ID (Perlica context ID)"),
) -> None:
    """进入交互会话模式 (Interactive chat mode)."""
    parent_obj = ctx.obj or {}
    resolved_provider = provider if provider is not None else parent_obj.get("provider")
    resolved_yes = yes or bool(parent_obj.get("yes"))
    resolved_context = context_id if context_id is not None else parent_obj.get("context_id")

    exit_code = _execute_chat(
        provider=resolved_provider,
        yes=resolved_yes,
        context_id=resolved_context,
    )
    raise typer.Exit(code=exit_code)


@app.command("doctor")
def doctor_cmd(
    verbose: bool = typer.Option(False, "--verbose", help="显示详细诊断信息 (Show detailed diagnostics)"),
    output_format: str = typer.Option(
        "json",
        "--format",
        help="输出格式：json|text (Output format)",
    ),
    context_id: Optional[str] = typer.Option(None, "--context", help="上下文 ID (Perlica context ID)"),
) -> None:
    normalized_format = output_format.strip().lower()
    if normalized_format not in {"json", "text"}:
        typer.echo(
            render_notice("error", "不支持的格式：{0}".format(output_format), "Unsupported format: {0}".format(output_format)),
            err=True,
        )
        raise typer.Exit(code=2)

    settings = load_settings(context_id=context_id)
    runtime = Runtime(settings)
    try:
        report = runtime.doctor(verbose=verbose)
        if normalized_format == "json":
            typer.echo(json.dumps(report, ensure_ascii=True, indent=2))
            return
        typer.echo(render_doctor_text(report))
    finally:
        runtime.close()


@skill_app.command("list")
def skill_list_cmd(
    context_id: Optional[str] = typer.Option(None, "--context", help="上下文 ID (Perlica context ID)"),
) -> None:
    settings = load_settings(context_id=context_id)
    runtime = Runtime(settings)
    try:
        skills = runtime.skill_engine.list_skills()
        if not skills:
            typer.echo(render_notice("info", "当前未加载任何技能。", "No skills loaded."))
            return
        for skill in skills:
            typer.echo(
                "{0} priority={1} triggers={2} source={3}".format(
                    skill.skill_id,
                    skill.priority,
                    ",".join(skill.triggers),
                    skill.source_path,
                )
            )
    finally:
        runtime.close()


@skill_app.command("reload")
def skill_reload_cmd(
    context_id: Optional[str] = typer.Option(None, "--context", help="上下文 ID (Perlica context ID)"),
) -> None:
    settings = load_settings(context_id=context_id)
    runtime = Runtime(settings)
    try:
        report = runtime.skill_engine.reload()
        typer.echo(
            render_notice(
                "success",
                "技能已重载：loaded={0} errors={1}".format(len(report.skills), len(report.errors)),
                "Reloaded skills: loaded={0} errors={1}".format(len(report.skills), len(report.errors)),
            )
        )
    finally:
        runtime.close()


@session_app.command("list")
def session_list_cmd(
    all_contexts: bool = typer.Option(False, "--all", help="查看所有 context 的会话 (List all contexts)"),
    context_id: Optional[str] = typer.Option(None, "--context", help="上下文 ID (Perlica context ID)"),
) -> None:
    settings = load_settings(context_id=context_id)
    if all_contexts:
        contexts_root = settings.config_root / "contexts"
        sessions: List[SessionRecord] = []
        current_by_context: Dict[str, Optional[str]] = {}

        if contexts_root.exists() and contexts_root.is_dir():
            for context_dir in sorted(contexts_root.iterdir()):
                if not context_dir.is_dir():
                    continue
                ctx_id = context_dir.name
                store = SessionStore(context_dir / "sessions.db")
                try:
                    sessions.extend(store.list_sessions(context_id=ctx_id, include_ephemeral=True))
                    current = store.get_current_session(ctx_id)
                    current_by_context[ctx_id] = current.session_id if current else None
                finally:
                    store.close()

        if not sessions:
            typer.echo(render_notice("info", "未找到会话。", "No sessions found."))
            return

        for session in sessions:
            typer.echo(
                _format_session_line(
                    session=session,
                    current_id=current_by_context.get(session.context_id),
                    include_context=True,
                )
            )
        return

    runtime = Runtime(settings)
    try:
        sessions = runtime.session_store.list_sessions(
            context_id=runtime.context_id,
            include_ephemeral=False,
        )
        if not sessions:
            typer.echo(render_notice("info", "未找到会话。", "No sessions found."))
            return

        current = runtime.session_store.get_current_session(runtime.context_id)
        current_id = current.session_id if current else None
        for session in sessions:
            typer.echo(
                _format_session_line(
                    session=session,
                    current_id=current_id,
                    include_context=False,
                )
            )
    finally:
        runtime.close()


@session_app.command("new")
def session_new_cmd(
    name: Optional[str] = typer.Option(None, "--name", help="会话别名（可选） (Optional session alias)"),
    provider: Optional[str] = typer.Option(
        None, "--provider", help="初始锁定的 provider（默认取配置） (Initial provider lock, default from config)"
    ),
    context_id: Optional[str] = typer.Option(None, "--context", help="上下文 ID (Perlica context ID)"),
) -> None:
    normalized_provider = _validate_provider(provider)
    if provider is not None and normalized_provider is None:
        raise typer.Exit(code=2)

    settings = load_settings(context_id=context_id, provider=normalized_provider)
    runtime = Runtime(settings)
    try:
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            name=name,
            provider_locked=settings.provider,
        )
        runtime.session_store.set_current_session(runtime.context_id, session.session_id)
        typer.echo(
            "已创建会话 (Created session): {0} name={1} provider={2}".format(
                session.session_id,
                session.name or "",
                session.provider_locked or "",
            )
        )
    finally:
        runtime.close()


@session_app.command("use")
def session_use_cmd(
    session_ref: str = typer.Argument(..., help="会话 ID/名称/前缀 (Session ref)"),
    context_id: Optional[str] = typer.Option(None, "--context", help="上下文 ID (Perlica context ID)"),
) -> None:
    settings = load_settings(context_id=context_id)
    runtime = Runtime(settings)
    try:
        session = runtime.session_store.resolve_session_ref(runtime.context_id, session_ref)
        runtime.session_store.set_current_session(runtime.context_id, session.session_id)
        typer.echo(
            "当前会话 (Current session): {0} name={1} provider={2}".format(
                session.session_id,
                session.name or "",
                session.provider_locked or "",
            )
        )
    except ValueError as exc:
        typer.echo(render_notice("error", str(exc)), err=True)
        raise typer.Exit(code=2)
    finally:
        runtime.close()


@session_app.command("current")
def session_current_cmd(
    context_id: Optional[str] = typer.Option(None, "--context", help="上下文 ID (Perlica context ID)"),
) -> None:
    settings = load_settings(context_id=context_id)
    runtime = Runtime(settings)
    try:
        current = runtime.session_store.get_current_session(runtime.context_id)
        if current is None:
            typer.echo(render_notice("info", "当前没有会话。", "No current session."))
            return
        typer.echo(
            "当前会话 (Current session): {0} name={1} provider={2}".format(
                current.session_id,
                current.name or "",
                current.provider_locked or "",
            )
        )
    finally:
        runtime.close()


@approvals_app.command("list")
def approvals_list_cmd(
    context_id: Optional[str] = typer.Option(None, "--context", help="上下文 ID (Perlica context ID)"),
) -> None:
    settings = load_settings(context_id=context_id)
    runtime = Runtime(settings)
    try:
        rows = runtime.approval_store.list_policies()
        if not rows:
            typer.echo(render_notice("info", "暂无已持久化审批偏好。", "No persisted approval preferences."))
            return
        for row in rows:
            typer.echo(
                "tool={0} risk={1} policy={2}".format(
                    row["tool_name"],
                    row["risk_tier"],
                    row["policy"],
                )
            )
    finally:
        runtime.close()


@approvals_app.command("reset")
def approvals_reset_cmd(
    all_entries: bool = typer.Option(False, "--all", help="重置所有审批偏好 (Reset all approvals)"),
    tool: Optional[str] = typer.Option(None, "--tool", help="工具名，例如 shell.exec (Tool name)"),
    risk: Optional[str] = typer.Option(None, "--risk", help="风险等级，例如 low|medium|high (Risk tier)"),
    context_id: Optional[str] = typer.Option(None, "--context", help="上下文 ID (Perlica context ID)"),
) -> None:
    settings = load_settings(context_id=context_id)
    runtime = Runtime(settings)
    try:
        if all_entries:
            deleted = runtime.approval_store.reset_all()
            typer.echo(
                render_notice(
                    "success",
                    "已重置 {0} 条审批偏好。".format(deleted),
                    "Reset {0} approval preference(s).".format(deleted),
                )
            )
            return

        if not tool or not risk:
            typer.echo(
                render_notice(
                    "error",
                    "请使用 --all，或同时提供 --tool 与 --risk。",
                    "Either --all or both --tool and --risk are required.",
                ),
                err=True,
            )
            raise typer.Exit(code=2)

        deleted = runtime.approval_store.reset(tool_name=tool, risk_tier=risk)
        typer.echo(
            render_notice(
                "success",
                "已重置 {0} 条审批偏好。".format(deleted),
                "Reset {0} approval preference(s).".format(deleted),
            )
        )
    finally:
        runtime.close()


if __name__ == "__main__":
    app()
