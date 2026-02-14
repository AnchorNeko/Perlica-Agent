"""Task state primitives for single-active-run orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class TaskState(str, Enum):
    """Lifecycle states for one active task."""

    IDLE = "idle"
    RUNNING = "running"
    AWAITING_INTERACTION = "awaiting_interaction"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskSnapshot:
    """Read-only view of current task status."""

    state: TaskState = TaskState.IDLE
    run_id: str = ""
    conversation_id: str = ""
    session_id: str = ""
    interaction_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_active_task(self) -> bool:
        return self.state in {TaskState.RUNNING, TaskState.AWAITING_INTERACTION}

    @property
    def waiting_interaction(self) -> bool:
        return self.state == TaskState.AWAITING_INTERACTION

