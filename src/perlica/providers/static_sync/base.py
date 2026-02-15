"""Base primitives and helpers for provider static syncers."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Tuple

from perlica.providers.static_sync.types import StaticSyncPayload, StaticSyncReport


class ProviderStaticSyncer(ABC):
    """Provider-specific static config sync contract."""

    @abstractmethod
    def provider_id(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def sync(self, payload: StaticSyncPayload) -> StaticSyncReport:
        raise NotImplementedError


def ensure_ascii_text(text: str) -> str:
    return str(text or "").encode("ascii", "backslashreplace").decode("ascii")


def load_json_object(path: Path) -> Dict[str, Any]:
    resolved = Path(path).expanduser()
    if not resolved.exists():
        return {}
    raw = resolved.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("json root must be an object: {0}".format(resolved))
    return payload


def write_json_if_changed(path: Path, payload: Dict[str, Any]) -> bool:
    resolved = Path(path).expanduser()
    rendered = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
    return write_text_if_changed(resolved, rendered)


def write_text_if_changed(path: Path, text: str) -> bool:
    resolved = Path(path).expanduser()
    current = ""
    if resolved.exists():
        current = resolved.read_text(encoding="utf-8")
        if current == text:
            return False
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(text, encoding="utf-8")
    return True


def is_writable_target(path: Path) -> bool:
    resolved = Path(path).expanduser()
    if resolved.exists():
        return os.access(resolved, os.W_OK)
    parent = resolved.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return os.access(parent, os.W_OK)


def select_scope_paths(
    *,
    scope_mode: str,
    project_mcp: Path,
    project_skills: Path,
    user_mcp: Path,
    user_skills: Path,
) -> Tuple[str, Path, Path, str]:
    mode = str(scope_mode or "project_first").strip().lower()
    if mode != "project_first":
        return "project", project_mcp, project_skills, ""

    project_writable = is_writable_target(project_mcp) and is_writable_target(project_skills)
    if project_writable:
        return "project", project_mcp, project_skills, ""

    note = "project scope not writable, fallback to user scope"
    return "user", user_mcp, user_skills, note

