from __future__ import annotations

import json
from pathlib import Path

from perlica.skills.engine import SkillEngine
from perlica.skills.loader import SkillLoader
from perlica.skills.schema import SkillSpec


def _builtin_skill_path() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / ".perlica_config" / "skills" / "macos-applescript-operator.skill.json"


def test_builtin_applescript_skill_file_is_valid():
    path = _builtin_skill_path()
    assert path.is_file()

    payload = json.loads(path.read_text(encoding="utf-8"))
    spec = SkillSpec.from_dict(payload, source_path=str(path))

    assert spec.skill_id == "macos-applescript-operator"
    assert spec.name == "macOS AppleScript Operator"
    assert spec.priority == 90
    assert "applescript" in spec.triggers
    assert "osascript" in spec.triggers
    assert "系统设置" in spec.triggers
    assert "打开应用" in spec.triggers
    assert "点击" in spec.triggers
    assert "osascript commands in here-doc form" in spec.system_prompt


def test_builtin_applescript_skill_trigger_match():
    repo_root = Path(__file__).resolve().parents[1]
    skills_dir = repo_root / ".perlica_config" / "skills"
    engine = SkillEngine(SkillLoader([skills_dir]))

    selection = engine.select("请用 AppleScript 打开 Safari 并点击书签栏第一个项目")
    selected_ids = [item.skill_id for item in selection.selected]

    assert "macos-applescript-operator" in selected_ids


def test_builtin_applescript_skill_priority_is_high():
    repo_root = Path(__file__).resolve().parents[1]
    skills_dir = repo_root / ".perlica_config" / "skills"
    engine = SkillEngine(SkillLoader([skills_dir]))

    skills = {item.skill_id: item for item in engine.list_skills()}
    assert "macos-applescript-operator" in skills
    assert skills["macos-applescript-operator"].priority == 90
