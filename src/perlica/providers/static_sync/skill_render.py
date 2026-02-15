"""Render provider-agnostic static skill markdown from Perlica skill specs."""

from __future__ import annotations

import json
import re

from perlica.providers.static_sync.base import ensure_ascii_text
from perlica.skills.schema import SkillSpec


def slugify_skill_id(skill_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(skill_id or "").strip().lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized or "skill"


def perlica_skill_dir_name(*, namespace_prefix: str, skill_id: str) -> str:
    return "{0}-{1}".format(
        slugify_skill_id(namespace_prefix),
        slugify_skill_id(skill_id),
    )


def render_skill_markdown(*, skill: SkillSpec, namespace_prefix: str) -> str:
    display_name = perlica_skill_dir_name(namespace_prefix=namespace_prefix, skill_id=skill.skill_id)
    description = str(skill.description or skill.name or display_name).strip() or display_name
    source_path = str(skill.source_path or "").strip()

    trigger_json = json.dumps(list(skill.triggers or []), ensure_ascii=True)
    system_prompt = str(skill.system_prompt or "").strip()
    system_prompt_block = ensure_ascii_text(system_prompt) if system_prompt else "(none)"

    lines = [
        "---",
        "name: {0}".format(ensure_ascii_text(display_name)),
        "description: {0}".format(ensure_ascii_text(description)),
        "---",
        "",
        "# {0}".format(ensure_ascii_text(display_name)),
        "",
        "Purpose",
        "This skill is synchronized from Perlica runtime skill registry.",
        "",
        "Source from Perlica",
        "- skill_id: {0}".format(ensure_ascii_text(skill.skill_id)),
        "- source_path: {0}".format(ensure_ascii_text(source_path)),
        "- triggers: {0}".format(ensure_ascii_text(trigger_json)),
        "",
        "Execution Rules",
        system_prompt_block,
        "",
    ]
    return "\n".join(lines)

