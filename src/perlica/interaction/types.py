"""Typed interaction contracts shared by ACP provider, TUI, and service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class InteractionOption:
    """One selectable option in a confirmation request."""

    index: int
    option_id: str
    label: str
    description: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InteractionRequest:
    """Pending interaction question emitted by provider notifications."""

    interaction_id: str
    question: str
    options: List[InteractionOption] = field(default_factory=list)
    allow_custom_input: bool = True
    source_method: str = ""
    conversation_id: str = ""
    run_id: str = ""
    trace_id: str = ""
    session_id: str = ""
    provider_id: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InteractionAnswer:
    """Resolved interaction answer selected by user."""

    interaction_id: str
    selected_index: Optional[int] = None
    selected_option_id: str = ""
    custom_text: str = ""
    source: str = ""
    conversation_id: str = ""
    run_id: str = ""
    trace_id: str = ""
    session_id: str = ""


@dataclass(frozen=True)
class InteractionSubmitResult:
    """Result of submitting a local or remote interaction answer."""

    accepted: bool
    message: str
    answer: Optional[InteractionAnswer] = None
