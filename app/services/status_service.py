"""State-change logic: persisting status and tracking gaming sessions.

Entering GAMING opens a session; leaving GAMING closes it. The post-game
digest hooks into the closed session (routing engine milestone).
"""
import logging

from app.models.status import AvailabilityState, StatusResponse, StatusUpdate
from app.services.digest_service import build_digest
from app.services.repositories import (
    DigestRepository,
    EventRepository,
    SessionRepository,
    StatusRepository,
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

        entering_game = (
            update.state == AvailabilityState.GAMING
            and previous.state != AvailabilityState.GAMING
        )
        leaving_game = (
            previous.state == AvailabilityState.GAMING
            and update.state != AvailabilityState.GAMING
        )
        # Switching games without leaving GAMING: close the old session (and its
        # digest) and open a new one, so each game gets its own recap (M7).
        switching_game = (
            update.state == AvailabilityState.GAMING
            and previous.state == AvailabilityState.GAMING
            and update.application != previous.application
        )

        if previous.state != update.state:
            log.info("State transition: %s -> %s", previous.state.value, update.state.value)

        closed_session = None
        if switching_game:
            # End the previous game's session (and its recap), then start fresh.
            closed_session = self._close_session()
            self._open_session(update)
        elif entering_game:
            self._open_session(update)
        elif leaving_game:
            closed_session = self._close_session()
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
        closed_session = self.session_repo.close_current()
        if closed_session:
            log.info(
                "Gaming session closed after %ss", closed_session["duration_seconds"]
            )
            if self.event_repo and self.digest_repo:
                self._create_digest(closed_session)
        return closed_session

    def _create_digest(self, session: dict) -> str:
        """Exactly one digest per ended session: queued events are consumed
        (marked delivered) so they can never appear in a second digest."""
        queued = self.event_repo.undelivered()
        digest = build_digest(session, queued)
        digest_id = self.digest_repo.add(session["id"], digest)
        self.event_repo.mark_delivered([e.id for e in queued], digest_id)
        return digest_id
