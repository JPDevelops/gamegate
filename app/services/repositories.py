"""Data access layer — where nearly all SQL lives. Two deliberate exceptions
own their queries: StatusService._close_session()'s transactional unit of work,
and the /art and /data/clear maintenance routes."""
import json
from datetime import UTC, datetime
from uuid import uuid4

from app.db import Database
from app.models.event import Event, EventIn
from app.models.status import AvailabilityState, StatusResponse, StatusUpdate


def _now() -> str:
    return datetime.now(UTC).isoformat()


class EventRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, incoming: EventIn, decision: str | None = None) -> tuple[Event, bool]:
        """Store an event. Returns (event, created). Idempotent on
        (source, external_id): a duplicate returns the original, untouched.
        Events that are delivered immediately or suppressed are marked consumed
        (delivered=1) so they never reappear in a digest."""
        event = Event(**incoming.model_dump())
        if decision is not None:
            event.metadata["routing"] = decision
        # Only 'suppress' is consumed at insert. A deliver-now event is NOT
        # marked consumed here: the caller queues its notification and then
        # calls mark_consumed(), so a crash between the two writes leaves the
        # event delivered=0 and it surfaces in the digest — a possible
        # duplicate, never a lost message (M6).
        consumed = decision == "suppress"
        conn = self.db.connection()
        # ON CONFLICT DO NOTHING makes concurrent duplicate posts race-safe:
        # whoever loses the race gets rowcount 0 and returns the stored
        # original instead of a 500.
        with conn:
            cursor = conn.execute(
                "INSERT INTO events (id, source, external_id, sender, title, content,"
                " received_at, priority, requires_action, metadata, created_at, delivered)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(source, external_id) DO NOTHING",
                (
                    event.id, event.source.value, event.external_id, event.sender,
                    event.title, event.content, event.received_at.isoformat(),
                    event.priority.value, int(event.requires_action),
                    json.dumps(event.metadata), event.created_at.isoformat(),
                    int(consumed),
                ),
            )
        if cursor.rowcount == 0:
            existing = self.find_by_external_id(
                incoming.source.value, incoming.external_id
            )
            return existing, False
        return event, True

    def find_by_external_id(self, source: str, external_id: str) -> Event | None:
        row = self.db.connection().execute(
            "SELECT * FROM events WHERE source = ? AND external_id = ?",
            (source, external_id),
        ).fetchone()
        return self._to_event(row) if row else None

    def find_by_id(self, event_id: str) -> Event | None:
        """Direct primary-key lookup — no scanning the recent window (M5)."""
        row = self.db.connection().execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        return self._to_event(row) if row else None

    def mark_consumed(self, event_id: str) -> None:
        """Mark a delivered-now event consumed so it won't re-appear in a
        digest. Called only after its notification row exists (M6)."""
        conn = self.db.connection()
        with conn:
            conn.execute("UPDATE events SET delivered = 1 WHERE id = ?", (event_id,))

    def recent(self, limit: int = 50) -> list[Event]:
        rows = self.db.connection().execute(
            "SELECT * FROM events ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._to_event(r) for r in rows]

    def undelivered(self, limit: int = 1000) -> list[Event]:
        # Cap the batch so one caller can't pull an unbounded backlog (e.g. a
        # weekend of 'away' with thousands of queued mails). NOTE: the recap uses
        # undelivered_in_window(), not this method, so an out-of-window backlog
        # is filtered in SQL and can't starve a game's messages. This plain
        # method is used where a bounded view of everything pending is wanted.
        rows = self.db.connection().execute(
            "SELECT * FROM events WHERE delivered = 0 AND priority != 'ignore'"
            " ORDER BY created_at ASC, rowid ASC LIMIT ?", (limit,)
        ).fetchall()
        return [self._to_event(r) for r in rows]

    def undelivered_in_window(
        self, start_iso: str, end_iso: str, limit: int = 1000
    ) -> list[Event]:
        """Undelivered events whose received_at falls inside [start, end].

        The window is filtered in SQL *before* the LIMIT, so the cap bounds the
        in-window rows we actually want — not an ever-growing prefix of stale,
        out-of-window 'away'/'focused' events that never get consumed. Doing the
        window filter in Python after `undelivered()` would let ≥1000 old queued
        events starve a game's real messages out of its recap (review MAJOR:
        LIMIT-before-filter). received_at is normalized to a UTC offset at the
        model boundary (Event._tz_aware uses astimezone), so every stored value
        shares the '+00:00' offset and the ISO-string BETWEEN compares
        chronologically."""
        rows = self.db.connection().execute(
            "SELECT * FROM events WHERE delivered = 0 AND priority != 'ignore'"
            " AND received_at >= ? AND received_at <= ?"
            " ORDER BY created_at ASC, rowid ASC LIMIT ?",
            (start_iso, end_iso, limit),
        ).fetchall()
        return [self._to_event(r) for r in rows]

    def mark_read(self, event_id: str, read: bool = True) -> bool:
        """Idempotent view-state flip. Returns False for unknown ids."""
        conn = self.db.connection()
        with conn:
            cur = conn.execute(
                "UPDATE events SET read_at = ? WHERE id = ?",
                (_now() if read else None, event_id),
            )
        return cur.rowcount > 0

    def mark_all_read(self) -> list[str]:
        conn = self.db.connection()
        with conn:  # SELECT + UPDATE in one transaction so a concurrent insert
            # can't slip between them and be missed by the returned ids (N17).
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM events WHERE read_at IS NULL"
            ).fetchall()]
            conn.executemany(
                "UPDATE events SET read_at = ? WHERE id = ?",
                [(_now(), event_id) for event_id in ids],
            )
        return ids

    def mark_delivered(self, event_ids: list[str], digest_id: str | None) -> None:
        conn = self.db.connection()
        with conn:
            conn.executemany(
                "UPDATE events SET delivered = 1, digest_id = ? WHERE id = ?",
                [(digest_id, eid) for eid in event_ids],
            )

    @staticmethod
    def _to_event(row) -> Event:
        return Event(
            id=row["id"], source=row["source"], external_id=row["external_id"],
            sender=row["sender"], title=row["title"], content=row["content"],
            received_at=row["received_at"], priority=row["priority"],
            requires_action=bool(row["requires_action"]),
            metadata=json.loads(row["metadata"]), created_at=row["created_at"],
            read_at=row["read_at"],
        )


class StatusRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self) -> StatusResponse:
        row = self.db.connection().execute("SELECT * FROM status WHERE id = 1").fetchone()
        if row is None:
            return StatusResponse(
                state=AvailabilityState.AVAILABLE, application=None, started_at=None
            )
        return StatusResponse(
            state=row["state"], application=row["application"], started_at=row["started_at"]
        )

    def set(self, update: StatusUpdate) -> StatusResponse:
        conn = self.db.connection()
        with conn:
            conn.execute(
                "INSERT INTO status (id, state, application, started_at, updated_at)"
                " VALUES (1, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET state = excluded.state,"
                " application = excluded.application, started_at = excluded.started_at,"
                " updated_at = excluded.updated_at",
                (
                    update.state.value, update.application,
                    update.started_at.isoformat() if update.started_at else None,
                    _now(),
                ),
            )
        return self.get()


class SessionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def open(
        self, application: str | None, started_at: str | None, app_id: str | None = None
    ) -> str | None:
        """Open a session ONLY if none is currently open (atomic guard against
        concurrent GAMING transitions each spawning a session)."""
        session_id = uuid4().hex
        conn = self.db.connection()
        with conn:
            cur = conn.execute(
                "INSERT INTO sessions (id, application, started_at, app_id)"
                " SELECT ?, ?, ?, ? WHERE NOT EXISTS"
                " (SELECT 1 FROM sessions WHERE ended_at IS NULL)",
                (session_id, application, started_at or _now(), app_id),
            )
        return session_id if cur.rowcount > 0 else None

    def current(self):
        return self.db.connection().execute(
            "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

    # NOTE: there is no close_current() here on purpose. Closing a session,
    # building its recap, and consuming its events are ONE transactional unit
    # that lives in StatusService._close_session() (so a crash can't leave a
    # closed session with no recap). A separate repo-level close was previously
    # kept only for a concurrency test; it was dead in production and gave a
    # false sense that the test covered the real path, so it was removed
    # (review MAJOR: test guarded dead code).


class DigestRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, session_id: str | None, body: dict) -> str:
        digest_id = uuid4().hex
        conn = self.db.connection()
        with conn:
            conn.execute(
                "INSERT INTO digests (id, session_id, created_at, body) VALUES (?, ?, ?, ?)",
                (digest_id, session_id, _now(), json.dumps(body)),
            )
        return digest_id

    def latest(self) -> dict | None:
        row = self.db.connection().execute(
            "SELECT * FROM digests ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return self._to_dict(row) if row else None

    def pending(self, limit: int = 200) -> list[dict]:
        rows = self.db.connection().execute(
            "SELECT * FROM digests WHERE delivered = 0 ORDER BY created_at ASC LIMIT ?",
            (limit,),  # bound the response (M11)
        ).fetchall()
        return [self._to_dict(r) for r in rows]

    def recent(self, limit: int = 10) -> list[dict]:
        rows = self.db.connection().execute(
            "SELECT * FROM digests ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_dict(r) for r in rows]

    def ack(self, digest_id: str) -> bool:
        conn = self.db.connection()
        with conn:
            cur = conn.execute(
                "UPDATE digests SET delivered = 1 WHERE id = ? AND delivered = 0",
                (digest_id,),
            )
        return cur.rowcount > 0

    @staticmethod
    def _to_dict(row) -> dict:
        return {
            "id": row["id"], "session_id": row["session_id"],
            "created_at": row["created_at"], **json.loads(row["body"]),
        }


class NotificationRepository:
    """Urgent break-through notifications waiting for a connector to push them."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, event_id: str) -> str:
        notification_id = uuid4().hex
        conn = self.db.connection()
        with conn:
            conn.execute(
                "INSERT INTO notifications (id, event_id, created_at) VALUES (?, ?, ?)",
                (notification_id, event_id, _now()),
            )
        return notification_id

    def pending(self, limit: int = 200) -> list[dict]:
        rows = self.db.connection().execute(
            "SELECT n.id AS notification_id, e.* FROM notifications n"
            " JOIN events e ON e.id = n.event_id"
            " WHERE n.delivered = 0 ORDER BY n.created_at ASC LIMIT ?",
            (limit,),  # bound the response (M11)
        ).fetchall()
        return [
            {
                "id": r["notification_id"],
                "event": EventRepository._to_event(r).model_dump(mode="json"),
            }
            for r in rows
        ]

    def ack(self, notification_id: str) -> bool:
        conn = self.db.connection()
        with conn:
            cur = conn.execute(
                "UPDATE notifications SET delivered = 1 WHERE id = ? AND delivered = 0",
                (notification_id,),
            )
        return cur.rowcount > 0
