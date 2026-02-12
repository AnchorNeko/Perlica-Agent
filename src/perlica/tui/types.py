"""Typed models for Perlica Textual chat UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ChatStatus:
    model: str
    session_title: str
    context_id: str
    phase: str = "就绪 (Ready)"


@dataclass
class SlashOutcome:
    handled: bool
    exit_requested: bool = False
    output_text: str = ""
    fallback_text: Optional[str] = None
