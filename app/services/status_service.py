"""State-change logic: persisting status and tracking gaming sessions.

Entering GAMING opens a session; leaving GAMING closes it. The post-game
digest hooks into the closed session (routing engine milestone).
"""
from app.models.status import AvailabilityState, StatusResponse, StatusUpdate
from app.services.digest_service import build_digest
from app.services.repositories import (
    DigestRepository,
    EventRepository,
    SessionRepository,
    StatusRepository,
)


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

        closed_session = None
        if entering_game:
            self.session_repo.open(
                update.application,
                update.started_at.isoformat() if update.started_at else None,
            )
        elif leaving_game:
            closed_session = self.session_repo.close_current()
            if closed_session and self.event_repo and self.digest_repo:
                self._create_digest(closed_session)
        return result, closed_session

    def _create_digest(self, session: dict) -> str:
        """Exactly one digest per ended session: queued events are consumed
        (marked delivered) so they can never appear in a second digest."""
        queued = self.event_repo.undelivered()
        digest = build_digest(session, queued)
        digest_id = self.digest_repo.add(session["id"], digest)
        self.event_repo.mark_delivered([e.id for e in queued], digest_id)
        return digest_id
