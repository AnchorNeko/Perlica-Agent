from __future__ import annotations

from io import StringIO

import perlica.repl as repl
from perlica.providers.static_sync.types import StaticSyncReport


def _report(provider_id: str) -> StaticSyncReport:
    report = StaticSyncReport(
        provider_id=provider_id,
        supported=True,
        scope="project",
        mcp_config_path="/tmp/.mcp.json",
        skills_root="/tmp/.claude/skills",
    )
    report.add_applied(
        kind="mcp",
        name="perlica.demo",
        path="/tmp/.mcp.json",
        action="updated",
    )
    report.add_applied(
        kind="skill",
        name="perlica-macos-applescript-operator",
        path="/tmp/.claude/skills/perlica-macos-applescript-operator/SKILL.md",
        action="updated",
    )
    return report


def test_start_repl_tty_runs_static_sync_summary(monkeypatch, isolated_env):
    sync_calls = []
    monkeypatch.setattr(repl, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(
        repl,
        "sync_provider_static_config",
        lambda **kwargs: sync_calls.append(kwargs["provider_id"]) or _report(kwargs["provider_id"]),
    )
    monkeypatch.setattr(repl, "run_startup_permission_checks", lambda **kwargs: {"checks": {}})
    monkeypatch.setattr(repl, "start_tui_chat", lambda provider, yes, context_id: 0)

    out = StringIO()
    err = StringIO()
    code = repl.start_repl(
        provider="claude",
        yes=False,
        context_id="default",
        run_executor=lambda *_args, **_kwargs: 0,
        stream=out,
        err_stream=err,
    )
    assert code == 0
    assert sync_calls == ["claude"]
    output = out.getvalue()
    assert "启动静态同步完成" in output
    assert "applied mcp:perlica.demo" in output
    assert "applied skill:perlica-macos-applescript-operator" in output


def test_start_service_mode_tty_runs_static_sync_summary(monkeypatch, isolated_env):
    sync_calls = []
    monkeypatch.setattr(repl, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(
        repl,
        "sync_provider_static_config",
        lambda **kwargs: sync_calls.append(kwargs["provider_id"]) or _report(kwargs["provider_id"]),
    )
    monkeypatch.setattr(repl, "run_startup_permission_checks", lambda **kwargs: {"checks": {}})
    monkeypatch.setattr(repl, "start_tui_service", lambda provider, yes, context_id: 0)

    out = StringIO()
    err = StringIO()
    code = repl.start_service_mode(
        provider="claude",
        yes=False,
        context_id="default",
        stream=out,
        err_stream=err,
    )
    assert code == 0
    assert sync_calls == ["claude"]
    output = out.getvalue()
    assert "启动静态同步完成" in output
    assert "applied mcp:perlica.demo" in output
