"""Skill schema definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class SkillSpec:
    skill_id: str
    name: str
    description: str
    triggers: List[str] = field(default_factory=list)
    priority: int = 0
    system_prompt: str = ""
    gates: Dict[str, Any] = field(default_factory=dict)
    source_path: str = ""

    @classmethod
    def from_dict(cls, payload: Dict[str, Any], source_path: str) -> "SkillSpec":
        skill_id = str(payload.get("id") or "").strip()
        if not skill_id:
            raise ValueError("skill id is required")

        name = str(payload.get("name") or skill_id)
        description = str(payload.get("description") or "")

        raw_triggers = payload.get("triggers") or []
        if not isinstance(raw_triggers, list):
            raise ValueError("triggers must be a list")

        triggers = [str(item).strip().lower() for item in raw_triggers if str(item).strip()]

        priority = int(payload.get("priority") or 0)
        system_prompt = str(payload.get("system_prompt") or "")
        gates = payload.get("gates") or {}
        if not isinstance(gates, dict):
            raise ValueError("gates must be an object")

        return cls(
            skill_id=skill_id,
            name=name,
            description=description,
            triggers=triggers,
            priority=priority,
            system_prompt=system_prompt,
            gates=gates,
            source_path=source_path,
        )
