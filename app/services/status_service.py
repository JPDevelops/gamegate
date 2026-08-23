"""State-change logic: persisting status and tracking gaming sessions.

Entering GAMING opens a session; leaving GAMING closes it. The post-game
digest hooks into the closed session (routing engine milestone).
"""
from app.models.status import AvailabilityState, StatusResponse, StatusUpdate
from app.services.repositories import SessionRepository, StatusRepository


class StatusService:
    def __init__(self, status_repo: StatusRepository, session_repo: SessionRepository):
        self.status_repo = status_repo
        self.session_repo = session_repo

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
        return result, closed_session
