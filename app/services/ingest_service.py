"""Event ingestion: store idempotently, route, and queue break-through
notifications for connectors to deliver.

Freshness rule (found live, 2026-08-23): when Gmail was first connected, its
initial sync ingested 31 old unread emails and every one popped an overlay —
a card parade. An event that was RECEIVED long before we ingest it is history,
not an interruption: stale events are queued for the digest, never delivered
now, regardless of priority.
"""
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.db import Database
from app.models.event import Event, EventIn
from app.services.repositories import (
    EventRepository,
    NotificationRepository,
    StatusRepository,
)
from app.services.routing import Decision, decide

FRESHNESS_WINDOW = timedelta(minutes=10)


def is_stale(incoming: EventIn) -> bool:
    received = incoming.received_at
    if received.tzinfo is None:
        received = received.replace(tzinfo=UTC)
    return datetime.now(UTC) - received > FRESHNESS_WINDOW


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
        if decision == Decision.DELIVER_NOW and is_stale(incoming):
            decision = Decision.QUEUE
        event, created = self.events.add(incoming, decision.value)
        if created and decision == Decision.DELIVER_NOW:
            self.notifications.add(event.id)
        return event, created, decision
