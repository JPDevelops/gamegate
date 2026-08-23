"""Event ingestion: store idempotently, route, and queue break-through
notifications for connectors to deliver."""
from app.config import Settings
from app.db import Database
from app.models.event import Event, EventIn
from app.services.repositories import (
    EventRepository,
    NotificationRepository,
    StatusRepository,
)
from app.services.routing import Decision, decide


class IngestService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.events = EventRepository(db)
        self.status = StatusRepository(db)
        self.notifications = NotificationRepository(db)
        self.settings = settings

    def ingest(self, incoming: EventIn) -> tuple[Event, bool, Decision]:
        state = self.status.get().state
        decision = decide(
            state, incoming.priority, self.settings.urgent_breaks_through_gaming
        )
        event, created = self.events.add(incoming, decision.value)
        if created and decision == Decision.DELIVER_NOW:
            self.notifications.add(event.id)
        return event, created, decision
