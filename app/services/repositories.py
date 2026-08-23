"""Data access layer. All SQL lives here — routes and services never touch it."""
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

    def add(self, incoming: EventIn) -> tuple[Event, bool]:
        """Store an event. Returns (event, created). Idempotent on
        (source, external_id): a duplicate returns the original, untouched."""
        existing = self.find_by_external_id(incoming.source.value, incoming.external_id)
        if existing is not None:
            return existing, False
        event = Event(**incoming.model_dump())
        conn = self.db.connection()
        with conn:
            conn.execute(
                "INSERT INTO events (id, source, external_id, sender, title, content,"
                " received_at, priority, requires_action, metadata, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.id, event.source.value, event.external_id, event.sender,
                    event.title, event.content, event.received_at.isoformat(),
                    event.priority.value, int(event.requires_action),
                    json.dumps(event.metadata), event.created_at.isoformat(),
                ),
            )
        return event, True

    def find_by_external_id(self, source: str, external_id: str) -> Event | None:
        row = self.db.connection().execute(
            "SELECT * FROM events WHERE source = ? AND external_id = ?",
            (source, external_id),
        ).fetchone()
        return self._to_event(row) if row else None

    def recent(self, limit: int = 50) -> list[Event]:
        rows = self.db.connection().execute(
            "SELECT * FROM events ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._to_event(r) for r in rows]

    def undelivered(self) -> list[Event]:
        rows = self.db.connection().execute(
            "SELECT * FROM events WHERE delivered = 0 AND priority != 'ignore'"
            " ORDER BY created_at ASC, rowid ASC"
        ).fetchall()
        return [self._to_event(r) for r in rows]

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

    def open(self, application: str | None, started_at: str | None) -> str:
        session_id = uuid4().hex
        conn = self.db.connection()
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, application, started_at) VALUES (?, ?, ?)",
                (session_id, application, started_at or _now()),
            )
        return session_id

    def current(self):
        return self.db.connection().execute(
            "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

    def close_current(self) -> dict | None:
        row = self.current()
        if row is None:
            return None
        ended = datetime.now(UTC)
        started = datetime.fromisoformat(row["started_at"])
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        duration = max(0, int((ended - started).total_seconds()))
        conn = self.db.connection()
        with conn:
            conn.execute(
                "UPDATE sessions SET ended_at = ?, duration_seconds = ? WHERE id = ?",
                (ended.isoformat(), duration, row["id"]),
            )
        return {
            "id": row["id"], "application": row["application"],
            "started_at": row["started_at"], "ended_at": ended.isoformat(),
            "duration_seconds": duration,
        }


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
        if row is None:
            return None
        return {
            "id": row["id"], "session_id": row["session_id"],
            "created_at": row["created_at"], **json.loads(row["body"]),
        }
