"""Deterministic digest builder. Same events in → same digest out. No AI."""
from app.models.event import Event, EventPriority

PRIORITY_ORDER = [
    EventPriority.URGENT,
    EventPriority.ACTIONABLE,
    EventPriority.INFORMATIONAL,
]


def build_digest(session: dict | None, events: list[Event]) -> dict:
    ordered = sorted(
        (e for e in events if e.priority != EventPriority.IGNORE),
        key=lambda e: (PRIORITY_ORDER.index(e.priority), e.received_at),
    )
    by_source: dict[str, int] = {}
    for event in ordered:
        by_source[event.source.value] = by_source.get(event.source.value, 0) + 1

    return {
        "session": session,
        "total_events": len(ordered),
        "counts_by_priority": {
            p.value: sum(1 for e in ordered if e.priority == p) for p in PRIORITY_ORDER
        },
        "counts_by_source": by_source,
        "action_required": [
            _item(e) for e in ordered if e.requires_action or e.priority == EventPriority.URGENT
        ],
        "items": [_item(e) for e in ordered],
    }


def render_text(digest: dict) -> str:
    lines = []
    session = digest.get("session")
    if session and session.get("duration_seconds") is not None:
        minutes, seconds = divmod(session["duration_seconds"], 60)
        hours, minutes = divmod(minutes, 60)
        duration = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {seconds:02d}s"
        app_name = session.get("application") or "unknown game"
        lines.append(f"Gaming session complete — {app_name}, {duration}")
    else:
        lines.append("Digest")

    counts = digest["counts_by_priority"]
    lines.append(
        f"{digest['total_events']} events while you were busy: "
        f"{counts['urgent']} urgent, {counts['actionable']} actionable, "
        f"{counts['informational']} informational."
    )
    for item in digest["action_required"]:
        lines.append(f"⚠ [{item['source'].upper()}] {item['sender']}: {item['title']}")
    for source, count in sorted(digest["counts_by_source"].items()):
        lines.append(f"{source.upper()} — {count} message(s)")
    return "\n".join(lines)


def _item(event: Event) -> dict:
    return {
        "id": event.id,
        "source": event.source.value,
        "sender": event.sender,
        "title": event.title,
        "priority": event.priority.value,
        "requires_action": event.requires_action,
        "received_at": event.received_at.isoformat(),
    }
