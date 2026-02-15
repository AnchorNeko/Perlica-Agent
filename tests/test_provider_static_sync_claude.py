from __future__ import annotations

import json
from pathlib import Path

from perlica.providers.static_sync.claude_sync import ClaudeStaticSyncer
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


def test_claude_static_sync_merges_and_cleans_stale_perlica_entries(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    mcp_file = workspace / ".mcp.json"
    mcp_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "external.keep": {"type": "stdio", "command": "echo", "args": ["ok"], "env": {}},
                    "perlica.old": {"type": "stdio", "command": "echo", "args": ["old"], "env": {}},
                }
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    skills_root = workspace / ".claude" / "skills"
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

    report = ClaudeStaticSyncer().sync(payload)
    assert report.scope == "project"
    assert report.failed == []

    merged = json.loads(mcp_file.read_text(encoding="utf-8"))
    rows = merged.get("mcpServers")
    assert isinstance(rows, dict)
    assert "external.keep" in rows
    assert "perlica.old" not in rows
    assert "perlica.demo" in rows
    assert rows["perlica.demo"]["type"] == "stdio"
    assert rows["perlica.demo"]["command"] == "python3"
    assert rows["perlica.demo"]["args"] == ["-m", "demo.server"]
    assert rows["perlica.demo"]["env"] == {"DEMO": "1"}

    expected_dir = perlica_skill_dir_name(
        namespace_prefix="perlica",
        skill_id="macos-applescript-operator",
    )
    expected_skill_file = skills_root / expected_dir / "SKILL.md"
    assert expected_skill_file.is_file()
    assert "Execution Rules" in expected_skill_file.read_text(encoding="utf-8")

    assert (skills_root / "custom-skill").is_dir()
    assert not stale_dir.exists()


def test_claude_static_sync_reports_invalid_mcp_shape(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    mcp_file = workspace / ".mcp.json"
    mcp_file.write_text('{"mcpServers": []}\n', encoding="utf-8")

    payload = StaticSyncPayload(
        workspace_dir=workspace,
        mcp_config_file=workspace / ".perlica_config" / "mcp" / "servers.toml",
        mcp_servers=[StaticMCPServer(server_id="demo", command="python3")],
        skills=[],
    )

    report = ClaudeStaticSyncer().sync(payload)
    assert report.has_failures is True
    assert any(item.kind == "mcp" and item.action == "invalid_shape" for item in report.failed)
