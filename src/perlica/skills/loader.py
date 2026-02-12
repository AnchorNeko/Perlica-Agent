"""Skill discovery and loading."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from perlica.skills.schema import SkillSpec


@dataclass
class SkillLoadReport:
    skills: Dict[str, SkillSpec] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)


class SkillLoader:
    """Loads skills from `*.skill.json` files with deterministic precedence."""

    def __init__(self, skill_dirs: List[Path]) -> None:
        self._skill_dirs = skill_dirs

    def load(self) -> SkillLoadReport:
        report = SkillLoadReport()

        for root in self._skill_dirs:
            if not root.exists() or not root.is_dir():
                continue

            for path in sorted(root.rglob("*.skill.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("skill file must contain a JSON object")
                    spec = SkillSpec.from_dict(payload, source_path=str(path))
                except Exception as exc:
                    report.errors[str(path)] = str(exc)
                    continue

                if spec.skill_id not in report.skills:
                    # Earlier search paths have higher priority.
                    report.skills[spec.skill_id] = spec

        return report
