"""Claude Code static config syncer."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict

from perlica.providers.static_sync.base import (
    ProviderStaticSyncer,
    load_json_object,
    select_scope_paths,
    write_json_if_changed,
    write_text_if_changed,
)
from perlica.providers.static_sync.skill_render import (
    perlica_skill_dir_name,
    render_skill_markdown,
)
from perlica.providers.static_sync.types import StaticSyncPayload, StaticSyncReport


class ClaudeStaticSyncer(ProviderStaticSyncer):
    def provider_id(self) -> str:
        return "claude"

    def sync(self, payload: StaticSyncPayload) -> StaticSyncReport:
        report = StaticSyncReport(provider_id=self.provider_id(), supported=True)

        scope, mcp_path, skills_root, note = select_scope_paths(
            scope_mode=payload.scope_mode,
            project_mcp=payload.workspace_dir / ".mcp.json",
            project_skills=payload.workspace_dir / ".claude" / "skills",
            user_mcp=Path("~/.claude/settings.json").expanduser(),
            user_skills=Path("~/.claude/skills").expanduser(),
        )
        report.scope = scope
        report.mcp_config_path = str(mcp_path)
        report.skills_root = str(skills_root)
        if note:
            report.notes.append(note)

        self._sync_mcp(payload=payload, report=report, mcp_path=mcp_path)
        self._sync_skills(payload=payload, report=report, skills_root=skills_root)
        return report

    def _sync_mcp(self, *, payload: StaticSyncPayload, report: StaticSyncReport, mcp_path: Path) -> None:
        desired = self._desired_mcp_entries(payload=payload)
        namespace_prefix = "{0}.".format(payload.namespace_prefix)

        try:
            root = load_json_object(mcp_path)
        except Exception as exc:
            report.add_failed(
                kind="mcp",
                name="config",
                path=str(mcp_path),
                action="load_failed",
                reason=str(exc),
            )
            return

        current_rows = root.get("mcpServers")
        if current_rows is None:
            merged_rows: Dict[str, Any] = {}
        elif isinstance(current_rows, dict):
            merged_rows = dict(current_rows)
        else:
            report.add_failed(
                kind="mcp",
                name="mcpServers",
                path=str(mcp_path),
                action="invalid_shape",
                reason="top-level `mcpServers` must be an object",
            )
            return

        existing_managed = [key for key in merged_rows.keys() if str(key).startswith(namespace_prefix)]
        if not desired and not existing_managed:
            report.add_skipped(
                kind="mcp",
                name="none",
                path=str(mcp_path),
                action="no_items",
                reason="no perlica mcp entries to sync",
            )
            return

        for name, row in desired.items():
            action = "updated" if merged_rows.get(name) != row else "unchanged"
            merged_rows[name] = row
            report.add_applied(kind="mcp", name=name, path=str(mcp_path), action=action)

        if payload.stale_cleanup:
            for name in sorted(list(merged_rows.keys())):
                if not str(name).startswith(namespace_prefix):
                    continue
                if name in desired:
                    continue
                merged_rows.pop(name, None)
                report.add_applied(kind="mcp", name=str(name), path=str(mcp_path), action="removed")

        root["mcpServers"] = merged_rows
        try:
            changed = write_json_if_changed(mcp_path, root)
            if not changed:
                report.notes.append("claude mcp config already up-to-date")
        except Exception as exc:
            report.add_failed(
                kind="mcp",
                name="config",
                path=str(mcp_path),
                action="write_failed",
                reason=str(exc),
            )

    def _sync_skills(self, *, payload: StaticSyncPayload, report: StaticSyncReport, skills_root: Path) -> None:
        managed_prefix = "{0}-".format(payload.namespace_prefix)
        desired_dirs: Dict[str, str] = {}

        for skill in payload.skills:
            skill_id = str(skill.skill_id or "").strip()
            if not skill_id:
                report.add_skipped(
                    kind="skill",
                    name="<empty>",
                    path=str(skills_root),
                    action="invalid_skill",
                    reason="missing skill_id",
                )
                continue
            dir_name = perlica_skill_dir_name(namespace_prefix=payload.namespace_prefix, skill_id=skill_id)
            if dir_name in desired_dirs:
                report.add_skipped(
                    kind="skill",
                    name=dir_name,
                    path=str(skills_root),
                    action="slug_collision",
                    reason="multiple skills render to the same directory name",
                )
                continue
            desired_dirs[dir_name] = render_skill_markdown(
                skill=skill,
                namespace_prefix=payload.namespace_prefix,
            )

        for dir_name, rendered in desired_dirs.items():
            skill_file = skills_root / dir_name / "SKILL.md"
            try:
                changed = write_text_if_changed(skill_file, rendered)
                report.add_applied(
                    kind="skill",
                    name=dir_name,
                    path=str(skill_file),
                    action="updated" if changed else "unchanged",
                )
            except Exception as exc:
                report.add_failed(
                    kind="skill",
                    name=dir_name,
                    path=str(skill_file),
                    action="write_failed",
                    reason=str(exc),
                )

        if not payload.stale_cleanup:
            return
        if not skills_root.exists():
            return
        if not skills_root.is_dir():
            report.add_failed(
                kind="skill",
                name="skills_root",
                path=str(skills_root),
                action="invalid_shape",
                reason="skills root exists but is not a directory",
            )
            return

        for child in sorted(skills_root.iterdir(), key=lambda item: item.name):
            if not child.is_dir():
                continue
            if not child.name.startswith(managed_prefix):
                continue
            if child.name in desired_dirs:
                continue
            try:
                shutil.rmtree(child)
                report.add_applied(kind="skill", name=child.name, path=str(child), action="removed")
            except Exception as exc:
                report.add_failed(
                    kind="skill",
                    name=child.name,
                    path=str(child),
                    action="remove_failed",
                    reason=str(exc),
                )

    @staticmethod
    def _desired_mcp_entries(payload: StaticSyncPayload) -> Dict[str, Dict[str, Any]]:
        rows: Dict[str, Dict[str, Any]] = {}
        for item in payload.mcp_servers:
            server_id = str(item.server_id or "").strip()
            command = str(item.command or "").strip()
            if not server_id:
                continue
            if not command:
                continue

            command_args = [str(arg) for arg in list(item.args or []) if str(arg).strip()]
            rows["{0}.{1}".format(payload.namespace_prefix, server_id)] = {
                "type": "stdio",
                "command": command,
                "args": command_args,
                "env": {str(k): str(v) for k, v in dict(item.env or {}).items()},
            }
        return rows
