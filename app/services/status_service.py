"""State-change logic: persisting status and tracking gaming sessions.

Entering GAMING opens a session; leaving GAMING closes it. The post-game
digest hooks into the closed session (routing engine milestone).
"""
import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from app.models.status import AvailabilityState, StatusResponse, StatusUpdate
from app.services.digest_service import build_digest
from app.services.repositories import (
    DigestRepository,
    EventRepository,
    SessionRepository,
    StatusRepository,
    _now,
)

log = logging.getLogger("gamegate.status")


class StatusService:
    def __init__(
        self,
        status_repo: StatusRepository,
        session_repo: SessionRepository,
        event_repo: EventRepository | None = None,
        digest_repo: DigestRepository | None = None,
    ):
        self.status_repo = status_repo
        self.session_repo = session_repo
        self.event_repo = event_repo
        self.digest_repo = digest_repo

    def get(self) -> StatusResponse:
        return self.status_repo.get()

    def set(self, update: StatusUpdate) -> tuple[StatusResponse, dict | None]:
        """Apply a status change. Returns (new_status, closed_session|None)."""
        previous = self.status_repo.get()
        result = self.status_repo.set(update)
        if previous.state != update.state:
            log.info("State transition: %s -> %s", previous.state.value, update.state.value)

        # Reconcile the session to the DESIRED state by comparing it to the
        # ACTUAL open session — not just to the previous status (review B1).
        # The status write and the session write are separate transactions, so a
        # crash between them used to leave an unrepairable "gaming with no
        # session" or "available with a session still open". Driving off the real
        # session state means the very next status poll self-heals it.
        current = self.session_repo.current()  # the open session row, or None
        closed_session = None
        if update.state == AvailabilityState.GAMING:
            if current is None:
                self._open_session(update)                    # heals gaming-with-no-session
            elif current["application"] != update.application:
                closed_session = self._close_session()        # game switch: close old...
                self._open_session(update)                    # ...and open the new one
            # else: the correct session is already open — nothing to do
        elif current is not None:
            closed_session = self._close_session()            # heals stuck-open session
        return result, closed_session

    def _open_session(self, update: StatusUpdate) -> None:
        opened = self.session_repo.open(
            update.application,
            update.started_at.isoformat() if update.started_at else None,
            update.app_id,
        )
        if opened:
            log.info("Gaming session opened (%s)", update.application)
        else:
            # open() returns None when a session is already open; don't swallow
            # it silently (M8) — a stuck-open session would then record nothing.
            log.warning(
                "Gaming session NOT opened for %s: a session is already open",
                update.application,
            )

    def _close_session(self) -> dict | None:
        """Close the current gaming session AND build its recap AND consume its
        events in ONE database transaction (review B2). A crash anywhere rolls
        the whole thing back — the session stays open and the next status poll
        reconciles and retries — so you can never end up with a closed session
        that has no recap and orphaned queued events. The close is rowcount-
        gated, so exactly one concurrent caller wins."""
        conn = self.session_repo.db.connection()
        row = self.session_repo.current()
        if row is None:
            return None
        ended = datetime.now(UTC)
        started = datetime.fromisoformat(row["started_at"])
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        duration = max(0, int((ended - started).total_seconds()))
        session = {
            "id": row["id"], "application": row["application"], "app_id": row["app_id"],
            "started_at": row["started_at"], "ended_at": ended.isoformat(),
            "duration_seconds": duration,
        }
        build = self.event_repo is not None and self.digest_repo is not None
        # Read the queued events and format the recap BEFORE the transaction
        # (pure work, no writes); the transaction re-marks these exact ids.
        queued = self.event_repo.undelivered() if build else []
        digest = build_digest(session, queued) if build else None
        digest_id = uuid4().hex
        with conn:  # one unit of work: close + digest + consume
            cur = conn.execute(
                "UPDATE sessions SET ended_at = ?, duration_seconds = ?"
                " WHERE id = ? AND ended_at IS NULL",
                (session["ended_at"], duration, row["id"]),
            )
            if cur.rowcount == 0:
                return None  # lost the close race — nothing else is written
            if build:
                conn.execute(
                    "INSERT INTO digests (id, session_id, created_at, body)"
                    " VALUES (?, ?, ?, ?)",
                    (digest_id, session["id"], _now(), json.dumps(digest)),
                )
                conn.executemany(
                    "UPDATE events SET delivered = 1, digest_id = ? WHERE id = ?",
                    [(digest_id, e.id) for e in queued],
                )
        log.info("Gaming session closed after %ss", duration)
        return session
