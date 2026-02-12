"""Skill selection engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from perlica.skills.loader import SkillLoadReport, SkillLoader
from perlica.skills.schema import SkillSpec


@dataclass
class SkillSelection:
    selected: List[SkillSpec] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)


class SkillEngine:
    """Resolves and selects skill candidates deterministically."""

    def __init__(self, loader: SkillLoader) -> None:
        self._loader = loader
        self._skills: Dict[str, SkillSpec] = {}
        self._errors: Dict[str, str] = {}
        self.reload()

    def reload(self) -> SkillLoadReport:
        report = self._loader.load()
        self._skills = report.skills
        self._errors = report.errors
        return report

    def list_skills(self) -> List[SkillSpec]:
        return [self._skills[key] for key in sorted(self._skills.keys())]

    def list_errors(self) -> Dict[str, str]:
        return dict(self._errors)

    def select(self, text: str) -> SkillSelection:
        query = text.lower()
        matched: List[SkillSpec] = []
        skipped: Dict[str, str] = {}

        for skill in self._skills.values():
            if not skill.triggers:
                skipped[skill.skill_id] = "no_triggers"
                continue

            if any(trigger in query for trigger in skill.triggers):
                matched.append(skill)
            else:
                skipped[skill.skill_id] = "trigger_not_matched"

        matched.sort(key=lambda item: (-item.priority, item.skill_id))
        return SkillSelection(selected=matched, skipped=skipped)

    @staticmethod
    def build_prompt_context(skills: List[SkillSpec]) -> str:
        if not skills:
            return ""
        blocks = []
        for skill in skills:
            if not skill.system_prompt:
                continue
            blocks.append("[{0}] {1}".format(skill.skill_id, skill.system_prompt))
        return "\n".join(blocks)
