from __future__ import annotations

from types import SimpleNamespace

import perlica.cli
from perlica.providers.static_sync.types import StaticSyncReport


class _FakeRuntime:
    def __init__(self, settings) -> None:
        self.settings = settings

    def close(self) -> None:
        return


class _FakeRunner:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def run_text(self, **kwargs):
        del kwargs
        return SimpleNamespace(assistant_text="ok")


def test_execute_prompt_triggers_startup_static_sync_and_prints_summary(
    isolated_env,
    monkeypatch,
    capsys,
):
    sync_calls = []

    def _fake_sync(*, settings, provider_id):
        sync_calls.append((settings.provider, provider_id))
        report = StaticSyncReport(
            provider_id=provider_id,
            supported=True,
            scope="project",
            mcp_config_path=str(settings.workspace_dir / ".mcp.json"),
            skills_root=str(settings.workspace_dir / ".claude" / "skills"),
        )
        report.add_applied(
            kind="mcp",
            name="perlica.demo",
            path=report.mcp_config_path,
            action="updated",
        )
        report.add_applied(
            kind="skill",
            name="perlica-macos-applescript-operator",
            path=str(settings.workspace_dir / ".claude" / "skills" / "perlica-macos-applescript-operator" / "SKILL.md"),
            action="updated",
        )
        return report

    monkeypatch.setattr(perlica.cli, "_resolve_provider_with_first_selection", lambda provider: ("claude", 0))
    monkeypatch.setattr(perlica.cli, "run_startup_permission_checks", lambda **kwargs: {"checks": {}})
    monkeypatch.setattr(perlica.cli, "sync_provider_static_config", _fake_sync)
    monkeypatch.setattr(perlica.cli, "Runtime", _FakeRuntime)
    monkeypatch.setattr(perlica.cli, "Runner", _FakeRunner)
    monkeypatch.setattr(perlica.cli, "render_assistant_panel", lambda *args, **kwargs: None)
    monkeypatch.setattr(perlica.cli, "render_run_meta", lambda *args, **kwargs: None)

    code = perlica.cli._execute_prompt(
        text="hello",
        provider="claude",
        yes=True,
        context_id="default",
        session_ref=None,
    )
    assert code == 0
    assert sync_calls == [("claude", "claude")]

    output = capsys.readouterr().out
    assert "启动静态同步完成" in output
    assert "applied mcp:perlica.demo" in output
    assert "applied skill:perlica-macos-applescript-operator" in output
