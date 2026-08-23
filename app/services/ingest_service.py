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
from app.models.event import Event, EventIn, EventPriority
from app.services.repositories import (
    EventRepository,
    NotificationRepository,
    StatusRepository,
)
from app.services.routing import Decision, decide
from app.services.settings_service import SettingsService, normalize_sender


def is_stale(incoming: EventIn, freshness_minutes: int = 10) -> bool:
    received = incoming.received_at
    if received.tzinfo is None:
        received = received.replace(tzinfo=UTC)
    return datetime.now(UTC) - received > timedelta(minutes=freshness_minutes)


class IngestService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.events = EventRepository(db)
        self.status = StatusRepository(db)
        self.notifications = NotificationRepository(db)
        self.settings = settings
        self.user_settings = SettingsService(db)

    def ingest(self, incoming: EventIn) -> tuple[Event, bool, Decision]:
        prefs = self.user_settings.get_all()

        # VIP senders and urgent keywords upgrade priority (never downgrade) —
        # server-side so the rule applies uniformly to every source.
        if incoming.priority != EventPriority.URGENT:
            sender = normalize_sender(incoming.sender)
            text = f"{incoming.title} {incoming.content}".lower()
            if sender and sender in prefs["vip_senders"]:
                incoming.priority = EventPriority.URGENT
                incoming.requires_action = True
            elif any(keyword in text for keyword in prefs["urgent_keywords"] if keyword):
                incoming.priority = EventPriority.URGENT

        state = self.status.get().state
        decision = decide(state, incoming.priority, prefs["urgent_breakthrough"])
        if decision == Decision.DELIVER_NOW and is_stale(
            incoming, prefs["freshness_minutes"]
        ):
            decision = Decision.QUEUE
        event, created = self.events.add(incoming, decision.value)
        if created and decision == Decision.DELIVER_NOW:
            self.notifications.add(event.id)
        return event, created, decision
