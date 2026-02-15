"""Startup static sync manager and payload builders."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from perlica.mcp.config import load_mcp_server_configs
from perlica.providers.static_sync.base import ProviderStaticSyncer
from perlica.providers.static_sync.claude_sync import ClaudeStaticSyncer
from perlica.providers.static_sync.opencode_sync import OpenCodeStaticSyncer
from perlica.providers.static_sync.types import StaticMCPServer, StaticSyncPayload, StaticSyncReport
from perlica.skills.loader import SkillLoader


class StaticSyncManager:
    """Routes startup static sync to provider-specific syncers."""

    def __init__(self, syncers: Optional[Sequence[ProviderStaticSyncer]] = None) -> None:
        resolved = list(syncers or [ClaudeStaticSyncer(), OpenCodeStaticSyncer()])
        self._syncers: Dict[str, ProviderStaticSyncer] = {}
        for syncer in resolved:
            key = str(syncer.provider_id() or "").strip().lower()
            if not key:
                continue
            self._syncers[key] = syncer

    def sync_for_provider(self, provider_id: str, payload: StaticSyncPayload) -> StaticSyncReport:
        normalized = str(provider_id or "").strip().lower()
        syncer = self._syncers.get(normalized)
        if syncer is None:
            report = StaticSyncReport(provider_id=normalized, supported=False, scope="none")
            report.add_skipped(
                kind="provider",
                name=normalized or "<empty>",
                path="",
                action="unsupported",
                reason="provider static sync unsupported",
            )
            self._append_payload_load_issues(report=report, payload=payload)
            return report

        try:
            report = syncer.sync(payload)
        except Exception as exc:
            report = StaticSyncReport(provider_id=normalized, supported=True, scope="none")
            report.add_failed(
                kind="provider",
                name=normalized,
                path="",
                action="sync_failed",
                reason=str(exc),
            )
        self._append_payload_load_issues(report=report, payload=payload)
        return report

    @staticmethod
    def _append_payload_load_issues(report: StaticSyncReport, payload: StaticSyncPayload) -> None:
        if payload.skip_mcp_reason:
            report.add_skipped(
                kind="mcp",
                name="perlica.*",
                path=str(payload.mcp_config_file),
                action="capability_disabled",
                reason=str(payload.skip_mcp_reason),
            )
        if payload.skip_skill_reason:
            report.add_skipped(
                kind="skill",
                name="perlica-*",
                path="",
                action="capability_disabled",
                reason=str(payload.skip_skill_reason),
            )
        if payload.mcp_load_errors:
            for index, error in enumerate(payload.mcp_load_errors):
                report.add_failed(
                    kind="mcp",
                    name="config[{0}]".format(index),
                    path=str(payload.mcp_config_file),
                    action="load_failed",
                    reason=str(error),
                )
        if payload.skill_load_errors:
            for source_path, error in sorted(payload.skill_load_errors.items()):
                report.add_failed(
                    kind="skill",
                    name=source_path,
                    path=str(source_path),
                    action="load_failed",
                    reason=str(error),
                )


def build_static_sync_payload(settings: object, provider_id: str = "") -> StaticSyncPayload:
    resolved_provider_id = str(provider_id or getattr(settings, "provider", "") or "").strip().lower()
    profile = _resolve_provider_profile(settings=settings, provider_id=resolved_provider_id)
    supports_mcp = _supports_capability(profile=profile, attr_name="supports_mcp_config")
    supports_skill = _supports_capability(profile=profile, attr_name="supports_skill_config")

    mcp_file = getattr(settings, "mcp_servers_file")
    workspace_dir = getattr(settings, "workspace_dir")
    skill_dirs = list(getattr(settings, "skill_dirs") or [])

    mcp_configs, mcp_errors = ([], [])
    if supports_mcp:
        mcp_configs, mcp_errors = load_mcp_server_configs(mcp_file)
    mcp_servers: List[StaticMCPServer] = []
    for row in mcp_configs:
        if not bool(getattr(row, "enabled", False)):
            continue
        mcp_servers.append(
            StaticMCPServer(
                server_id=str(getattr(row, "server_id", "") or "").strip(),
                command=str(getattr(row, "command", "") or "").strip(),
                args=[str(arg) for arg in list(getattr(row, "args", []) or []) if str(arg).strip()],
                env={str(key): str(value) for key, value in dict(getattr(row, "env", {}) or {}).items()},
            )
        )

    skill_report = SkillLoader(skill_dirs).load() if supports_skill else None
    skills = []
    if skill_report is not None:
        skills = [skill_report.skills[key] for key in sorted(skill_report.skills.keys())]

    return StaticSyncPayload(
        workspace_dir=workspace_dir,
        mcp_config_file=mcp_file,
        mcp_servers=mcp_servers,
        skills=skills,
        mcp_load_errors=list(mcp_errors),
        skill_load_errors=dict(skill_report.errors) if skill_report is not None else {},
        skip_mcp_reason="" if supports_mcp else "capability supports_mcp_config=false",
        skip_skill_reason="" if supports_skill else "capability supports_skill_config=false",
    )


def sync_provider_static_config(*, settings: object, provider_id: str) -> StaticSyncReport:
    manager = StaticSyncManager()
    payload = build_static_sync_payload(settings, provider_id=provider_id)
    return manager.sync_for_provider(provider_id, payload)


def static_sync_notice(report: object) -> Tuple[str, str, str, bool]:
    has_failures = bool(getattr(report, "has_failures", False))
    supported = bool(getattr(report, "supported", False))
    provider_id = str(getattr(report, "provider_id", "") or "")
    scope = str(getattr(report, "scope", "none") or "none")
    if not supported:
        return (
            "info",
            "已跳过 provider 静态同步：provider={0}".format(provider_id or "<empty>"),
            "Provider static sync skipped: provider={0}".format(provider_id or "<empty>"),
            False,
        )
    return (
        "warn" if has_failures else "success",
        "启动静态同步完成：provider={0} scope={1}".format(provider_id, scope),
        "Startup static sync completed: provider={0} scope={1}".format(provider_id, scope),
        has_failures,
    )


def format_static_sync_report_lines(report: StaticSyncReport) -> List[str]:
    lines: List[str] = []
    if not report.supported:
        lines.append(
            "provider={0} static_sync=skipped reason=unsupported".format(report.provider_id or "<empty>")
        )
    else:
        lines.append(
            "provider={0} scope={1} mcp_config={2} skills_root={3}".format(
                report.provider_id or "<empty>",
                report.scope or "none",
                report.mcp_config_path or "",
                report.skills_root or "",
            )
        )

    lines.extend(_format_items(prefix="applied", rows=report.applied))
    lines.extend(_format_items(prefix="skipped", rows=report.skipped))
    lines.extend(_format_items(prefix="failed", rows=report.failed))

    for note in report.notes:
        lines.append("note={0}".format(str(note)))
    return lines


def _format_items(prefix: str, rows: Iterable[object]) -> List[str]:
    lines: List[str] = []
    for row in rows:
        kind = str(getattr(row, "kind", "") or "").strip()
        name = str(getattr(row, "name", "") or "").strip()
        path = str(getattr(row, "path", "") or "").strip()
        action = str(getattr(row, "action", "") or "").strip()
        reason = str(getattr(row, "reason", "") or "").strip()

        line = "{0} {1}:{2} action={3}".format(prefix, kind, name, action)
        if path:
            line = "{0} path={1}".format(line, path)
        if reason:
            line = "{0} reason={1}".format(line, reason)
        lines.append(line)
    return lines


def _resolve_provider_profile(settings: object, provider_id: str) -> Optional[object]:
    profiles = getattr(settings, "provider_profiles", None)
    if isinstance(profiles, dict):
        profile = profiles.get(provider_id)
        if profile is not None:
            return profile
    active_profile = getattr(settings, "provider_profile", None)
    if active_profile is not None:
        return active_profile
    return None


def _supports_capability(*, profile: Optional[object], attr_name: str) -> bool:
    if profile is None:
        return True
    value = getattr(profile, attr_name, None)
    if isinstance(value, bool):
        return value
    return bool(value) if value is not None else True
