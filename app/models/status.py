from datetime import datetime
from enum import Enum

from pydantic import BaseModel, field_validator


class AvailabilityState(str, Enum):
    AVAILABLE = "available"
    FOCUSED = "focused"
    GAMING = "gaming"
    AWAY = "away"


class StatusUpdate(BaseModel):
    state: AvailabilityState
    application: str | None = None
    started_at: datetime | None = None
    app_id: str | None = None  # e.g. Steam appid, for artwork

    @field_validator("application")
    @classmethod
    def _clean_application(cls, v: str | None) -> str | None:
        """A game/process name. Strip control characters and cap the length so
        it can't inject forged lines into journald when logged (review M4)."""
        if v is None:
            return None
        cleaned = "".join(ch for ch in v if ch == " " or ch.isprintable())
        return cleaned[:128]


class StatusResponse(BaseModel):
    state: AvailabilityState
    application: str | None
    started_at: datetime | None
