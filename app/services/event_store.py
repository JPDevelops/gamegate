"""In-memory event storage.

Deliberately not persistent and not yet idempotent — both arrive with the
database in Step 4 of the runbook.
"""
from app.models.event import Event, EventIn


class EventStore:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._events: list[Event] = []

    def add(self, incoming: EventIn) -> Event:
        event = Event(**incoming.model_dump())
        self._events.append(event)
        return event

    def recent(self, limit: int = 50) -> list[Event]:
        return list(reversed(self._events))[:limit]


event_store = EventStore()
