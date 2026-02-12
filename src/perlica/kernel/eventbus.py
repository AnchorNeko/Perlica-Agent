"""In-process event bus for decoupled listeners."""

from __future__ import annotations

from collections import defaultdict
from typing import DefaultDict, List

from perlica.kernel.types import EventEnvelope, EventHandler


class EventBus:
    """Simple pub-sub implementation scoped to one process."""

    def __init__(self) -> None:
        self._subscribers: DefaultDict[str, List[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._subscribers[event_type].append(handler)

    def publish(self, event: EventEnvelope) -> None:
        handlers = list(self._subscribers.get(event.event_type, []))
        handlers += list(self._subscribers.get("*", []))
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                # Event handlers are isolated from the main execution flow.
                continue
