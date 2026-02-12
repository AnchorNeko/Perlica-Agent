from __future__ import annotations

import json
from pathlib import Path

from perlica.skills.engine import SkillEngine
from perlica.skills.loader import SkillLoader


def _write_skill(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_skill_loader_path_precedence(tmp_path: Path):
    high = tmp_path / "high"
    low = tmp_path / "low"

    _write_skill(
        high / "same.skill.json",
        {
            "id": "same",
            "name": "Same High",
            "description": "high",
            "triggers": ["deploy"],
            "priority": 10,
            "system_prompt": "high prompt",
        },
    )
    _write_skill(
        low / "same.skill.json",
        {
            "id": "same",
            "name": "Same Low",
            "description": "low",
            "triggers": ["deploy"],
            "priority": 1,
            "system_prompt": "low prompt",
        },
    )

    engine = SkillEngine(SkillLoader([high, low]))
    skills = engine.list_skills()

    assert len(skills) == 1
    assert skills[0].name == "Same High"


def test_skill_selection_priority_and_stability(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir / "a.skill.json",
        {
            "id": "a",
            "name": "A",
            "description": "A",
            "triggers": ["build"],
            "priority": 5,
            "system_prompt": "A",
        },
    )
    _write_skill(
        skills_dir / "b.skill.json",
        {
            "id": "b",
            "name": "B",
            "description": "B",
            "triggers": ["build"],
            "priority": 5,
            "system_prompt": "B",
        },
    )

    engine = SkillEngine(SkillLoader([skills_dir]))
    selection = engine.select("please build project")

    assert [skill.skill_id for skill in selection.selected] == ["a", "b"]


def test_skill_reload_picks_new_files(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    engine = SkillEngine(SkillLoader([skills_dir]))
    assert engine.list_skills() == []

    _write_skill(
        skills_dir / "new.skill.json",
        {
            "id": "new",
            "name": "New",
            "description": "new",
            "triggers": ["calendar"],
            "priority": 1,
            "system_prompt": "new",
        },
    )

    report = engine.reload()
    assert "new" in report.skills
