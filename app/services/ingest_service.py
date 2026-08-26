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


def message_identity(source: str, sender: str, title: str) -> str:
    """The 'who' a message is from, used to key the user's urgent / not-urgent
    marks. Captured apps (text/discord/slack/system) put the app label in
    `sender` and the real sender/number/channel in `title`; connector sources
    (gmail) put the real sender in `sender`. Normalized to match stored rules."""
    if source in ("text", "discord", "slack", "system"):
        return (title or sender or "").strip().lower()
    return normalize_sender(sender) or (title or "").strip().lower()


class IngestService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.events = EventRepository(db)
        self.status = StatusRepository(db)
        self.notifications = NotificationRepository(db)
        self.settings = settings
        self.user_settings = SettingsService(db)

    def ingest(self, incoming: EventIn) -> tuple[Event, bool, Decision]:
        # Work on a copy — priority upgrades below must not mutate the caller's
        # object (N40).
        incoming = incoming.model_copy()
        prefs = self.user_settings.get_all()

        # The 'who' the user's marks key on, and whether they marked this sender
        # "not urgent" (that mark overrides VIP/keyword/AI — the user always wins).
        who = message_identity(incoming.source.value, incoming.sender, incoming.title)
        never_urgent = bool(who) and who in prefs.get("never_urgent_senders", [])

        # VIP senders and urgent keywords upgrade priority (never downgrade) —
        # server-side so the rule applies uniformly to every source. Skipped for a
        # not-urgent sender.
        if incoming.priority != EventPriority.URGENT and not never_urgent:
            sender = normalize_sender(incoming.sender)
            text = f"{incoming.title} {incoming.content}".lower()
            # VIP matches the 'who' OR the raw email (back-compat email VIP lists).
            if (who and who in prefs["vip_senders"]) or (sender and sender in prefs["vip_senders"]):
                incoming.priority = EventPriority.URGENT
                incoming.requires_action = True
            elif any(keyword in text for keyword in prefs["urgent_keywords"] if keyword):
                incoming.priority = EventPriority.URGENT

        # Opt-in AI classifier: attach a one-line summary always; it only ESCALATES
        # priority when the user hasn't marked this sender not-urgent. Never raises.
        if incoming.priority != EventPriority.URGENT and prefs.get("classifier_enabled"):
            self._ai_classify(incoming, escalate=not never_urgent)

        # Belt-and-suspenders: the source (or anything) may have set it urgent, but
        # a "not urgent" mark for this sender wins — hold it, don't break through.
        if never_urgent and incoming.priority == EventPriority.URGENT:
            incoming.priority = EventPriority.INFORMATIONAL
            incoming.requires_action = False

        state = self.status.get().state
        decision = decide(
            state, incoming.priority, prefs["urgent_breakthrough"],
            prefs.get("notify_when_available", True), prefs.get("ping_non_urgent", False),
        )
        if decision == Decision.DELIVER_NOW and is_stale(
            incoming, prefs["freshness_minutes"]
        ):
            decision = Decision.QUEUE
        event, created = self.events.add(incoming, decision.value)
        if created and decision == Decision.DELIVER_NOW:
            # Queue the notification FIRST, then mark the event consumed. If we
            # die between these, the event is still delivered=0 and lands in the
            # digest (at-worst a duplicate, never a lost message — M6).
            self.notifications.add(event.id)
            self.events.mark_consumed(event.id)
        return event, created, decision

    def _ai_classify(self, incoming: EventIn, escalate: bool = True) -> None:
        """Run the LLM classifier and fold its verdict into the event: attach a
        one-line summary for the recap, and (when `escalate`) refine priority
        (never below its current level). `escalate=False` for a sender the user
        marked not-urgent — we still want the summary, just not the escalation.
        Best-effort — SafeClassifier never raises."""
        from app.services.classifier import build_classifier

        classifier = build_classifier()
        if classifier.primary is None:
            return  # enabled flag on but no usable key — nothing to do
        result = classifier.classify(incoming)
        if escalate:
            if result.category == "action_required" or result.urgency >= 8:
                incoming.priority = EventPriority.URGENT
                incoming.requires_action = True
            elif result.urgency >= 5 and incoming.priority == EventPriority.INFORMATIONAL:
                incoming.priority = EventPriority.ACTIONABLE
        meta = dict(incoming.metadata or {})
        meta["ai"] = {
            "summary": result.summary,
            "action": result.suggested_action,
            "urgency": result.urgency,
            "category": result.category,
        }
        incoming.metadata = meta
