"""Shared types for provider startup static sync."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from perlica.skills.schema import SkillSpec


@dataclass(frozen=True)
class StaticMCPServer:
    server_id: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class StaticSyncPayload:
    workspace_dir: Path
    mcp_config_file: Path
    scope_mode: str = "project_first"
    mcp_servers: List[StaticMCPServer] = field(default_factory=list)
    skills: List[SkillSpec] = field(default_factory=list)
    stale_cleanup: bool = True
    namespace_prefix: str = "perlica"
    mcp_load_errors: List[str] = field(default_factory=list)
    skill_load_errors: Dict[str, str] = field(default_factory=dict)
    skip_mcp_reason: str = ""
    skip_skill_reason: str = ""


@dataclass(frozen=True)
class StaticSyncItemReport:
    kind: str
    name: str
    path: str
    action: str
    reason: str = ""


@dataclass
class StaticSyncReport:
    provider_id: str
    supported: bool = True
    scope: str = "none"
    mcp_config_path: str = ""
    skills_root: str = ""
    applied: List[StaticSyncItemReport] = field(default_factory=list)
    skipped: List[StaticSyncItemReport] = field(default_factory=list)
    failed: List[StaticSyncItemReport] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def add_applied(self, *, kind: str, name: str, path: str, action: str) -> None:
        self.applied.append(
            StaticSyncItemReport(kind=kind, name=name, path=path, action=action)
        )

    def add_skipped(self, *, kind: str, name: str, path: str, action: str, reason: str) -> None:
        self.skipped.append(
            StaticSyncItemReport(kind=kind, name=name, path=path, action=action, reason=reason)
        )

    def add_failed(self, *, kind: str, name: str, path: str, action: str, reason: str) -> None:
        self.failed.append(
            StaticSyncItemReport(kind=kind, name=name, path=path, action=action, reason=reason)
        )

    @property
    def has_failures(self) -> bool:
        return bool(self.failed)
