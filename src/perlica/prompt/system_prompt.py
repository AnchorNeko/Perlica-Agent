"""System prompt loading from project configuration."""

from __future__ import annotations

from pathlib import Path

from perlica.config import Settings


class PromptLoadError(RuntimeError):
    """Raised when runtime system prompt cannot be loaded."""


def load_system_prompt(settings: Settings) -> str:
    path = Path(settings.system_prompt_file)
    if not path.is_file():
        raise PromptLoadError(
            "缺少系统提示词文件：{0}，请执行 `perlica init --force` 或恢复该文件。".format(path)
        )
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        raise PromptLoadError("读取系统提示词失败：{0}".format(path)) from exc
    if not text:
        raise PromptLoadError(
            "系统提示词为空：{0}，请填充提示内容后重试。".format(path)
        )
    return text
