"""In-memory holder for the current availability status.

Deliberately not persistent — the database arrives with Step 4 of the runbook.
"""
from app.models.status import AvailabilityState, StatusResponse, StatusUpdate


class StatusStore:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._current = StatusResponse(
            state=AvailabilityState.AVAILABLE, application=None, started_at=None
        )

    def get(self) -> StatusResponse:
        return self._current

    def set(self, update: StatusUpdate) -> StatusResponse:
        self._current = StatusResponse(
            state=update.state,
            application=update.application,
            started_at=update.started_at,
        )
        return self._current


status_store = StatusStore()
