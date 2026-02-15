from __future__ import annotations

from pathlib import Path

import perlica.providers.static_sync.base as static_sync_base
from perlica.providers.static_sync.base import ProviderStaticSyncer
from perlica.providers.static_sync.manager import StaticSyncManager
from perlica.providers.static_sync.types import StaticSyncPayload, StaticSyncReport


class _FakeSyncer(ProviderStaticSyncer):
    def __init__(self, provider_name: str) -> None:
        self._provider_name = provider_name
        self.calls = 0

    def provider_id(self) -> str:
        return self._provider_name

    def sync(self, payload: StaticSyncPayload) -> StaticSyncReport:
        del payload
        self.calls += 1
        return StaticSyncReport(
            provider_id=self._provider_name,
            supported=True,
            scope="project",
            mcp_config_path="/tmp/x.json",
            skills_root="/tmp/skills",
        )


def _payload(workspace_dir: Path) -> StaticSyncPayload:
    return StaticSyncPayload(
        workspace_dir=workspace_dir,
        mcp_config_file=workspace_dir / ".perlica_config" / "mcp" / "servers.toml",
    )


def test_manager_routes_to_matching_provider_syncer(tmp_path: Path):
    claude = _FakeSyncer("claude")
    opencode = _FakeSyncer("opencode")
    manager = StaticSyncManager(syncers=[claude, opencode])

    report = manager.sync_for_provider("opencode", _payload(tmp_path))
    assert report.provider_id == "opencode"
    assert report.scope == "project"
    assert opencode.calls == 1
    assert claude.calls == 0


def test_manager_skips_unsupported_provider(tmp_path: Path):
    manager = StaticSyncManager(syncers=[_FakeSyncer("claude")])
    report = manager.sync_for_provider("unknown", _payload(tmp_path))
    assert report.supported is False
    assert report.scope == "none"
    assert report.skipped
    assert report.skipped[0].action == "unsupported"


def test_manager_project_first_falls_back_to_user_scope(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))

    original = static_sync_base.is_writable_target

    def _fake_is_writable_target(path: Path) -> bool:
        text = str(path)
        if text.startswith(str(fake_home)):
            return True
        if text.startswith(str(workspace)):
            return False
        return original(path)

    monkeypatch.setattr(static_sync_base, "is_writable_target", _fake_is_writable_target)

    manager = StaticSyncManager()
    report = manager.sync_for_provider("claude", _payload(workspace))
    assert report.supported is True
    assert report.scope == "user"
    assert report.notes
    assert any("fallback" in note for note in report.notes)


def test_manager_marks_capability_disabled_items_as_skipped(tmp_path: Path):
    manager = StaticSyncManager(syncers=[_FakeSyncer("claude")])
    payload = StaticSyncPayload(
        workspace_dir=tmp_path,
        mcp_config_file=tmp_path / ".perlica_config" / "mcp" / "servers.toml",
        skip_mcp_reason="capability supports_mcp_config=false",
        skip_skill_reason="capability supports_skill_config=false",
    )

    report = manager.sync_for_provider("claude", payload)
    actions = {(row.kind, row.action) for row in report.skipped}
    assert ("mcp", "capability_disabled") in actions
    assert ("skill", "capability_disabled") in actions
