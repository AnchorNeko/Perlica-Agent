from __future__ import annotations

import json
from pathlib import Path

from perlica.providers.static_sync.opencode_sync import OpenCodeStaticSyncer
from perlica.providers.static_sync.skill_render import perlica_skill_dir_name
from perlica.providers.static_sync.types import StaticMCPServer, StaticSyncPayload
from perlica.skills.schema import SkillSpec


def _skill(skill_id: str = "macos-applescript-operator") -> SkillSpec:
    return SkillSpec(
        skill_id=skill_id,
        name="macOS AppleScript Operator",
        description="Prefer AppleScript for GUI operations.",
        triggers=["applescript", "osascript"],
        priority=90,
        system_prompt="Prefer AppleScript here-doc commands.",
        source_path=".perlica_config/skills/macos-applescript-operator.skill.json",
    )


def test_opencode_static_sync_merges_and_cleans_stale_perlica_entries(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    opencode_json = workspace / "opencode.json"
    opencode_json.write_text(
        json.dumps(
            {
                "mcp": {
                    "external.keep": {"type": "local", "command": ["echo", "ok"], "enabled": True},
                    "perlica.old": {"type": "local", "command": ["echo", "old"], "enabled": True},
                }
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    skills_root = workspace / ".opencode" / "skills"
    (skills_root / "custom-skill").mkdir(parents=True, exist_ok=True)
    stale_dir = skills_root / "perlica-old-skill"
    stale_dir.mkdir(parents=True, exist_ok=True)
    (stale_dir / "SKILL.md").write_text("# stale\n", encoding="utf-8")

    payload = StaticSyncPayload(
        workspace_dir=workspace,
        mcp_config_file=workspace / ".perlica_config" / "mcp" / "servers.toml",
        mcp_servers=[
            StaticMCPServer(
                server_id="demo",
                command="python3",
                args=["-m", "demo.server"],
                env={"DEMO": "1"},
            )
        ],
        skills=[_skill()],
        stale_cleanup=True,
        namespace_prefix="perlica",
    )

    report = OpenCodeStaticSyncer().sync(payload)
    assert report.scope == "project"
    assert report.failed == []

    merged = json.loads(opencode_json.read_text(encoding="utf-8"))
    mcp_rows = merged.get("mcp")
    assert isinstance(mcp_rows, dict)
    assert "external.keep" in mcp_rows
    assert "perlica.old" not in mcp_rows
    assert "perlica.demo" in mcp_rows
    assert mcp_rows["perlica.demo"]["type"] == "local"
    assert mcp_rows["perlica.demo"]["command"] == ["python3", "-m", "demo.server"]
    assert mcp_rows["perlica.demo"]["environment"] == {"DEMO": "1"}
    assert mcp_rows["perlica.demo"]["enabled"] is True

    expected_dir = perlica_skill_dir_name(
        namespace_prefix="perlica",
        skill_id="macos-applescript-operator",
    )
    expected_skill_file = skills_root / expected_dir / "SKILL.md"
    assert expected_skill_file.is_file()
    assert "Source from Perlica" in expected_skill_file.read_text(encoding="utf-8")

    assert (skills_root / "custom-skill").is_dir()
    assert not stale_dir.exists()


def test_opencode_static_sync_reports_invalid_mcp_shape(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    opencode_json = workspace / "opencode.json"
    opencode_json.write_text('{"mcp": []}\n', encoding="utf-8")

    payload = StaticSyncPayload(
        workspace_dir=workspace,
        mcp_config_file=workspace / ".perlica_config" / "mcp" / "servers.toml",
        mcp_servers=[StaticMCPServer(server_id="demo", command="python3")],
        skills=[],
    )

    report = OpenCodeStaticSyncer().sync(payload)
    assert report.has_failures is True
    assert any(item.kind == "mcp" and item.action == "invalid_shape" for item in report.failed)
