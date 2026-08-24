from datetime import UTC, datetime, timedelta
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

    @field_validator("application", "app_id")
    @classmethod
    def _clean_text(cls, v: str | None) -> str | None:
        """Game name / launcher app id. Strip control characters and cap the
        length — `application` gets logged (journald injection) and `app_id`
        flows into an image URL, so bound both symmetrically (review M4, N10)."""
        if v is None:
            return None
        cleaned = "".join(ch for ch in v if ch == " " or ch.isprintable())
        return cleaned[:128]

    @field_validator("started_at")
    @classmethod
    def _normalize_started_at(cls, v: datetime | None) -> datetime | None:
        """The detector supplies started_at, so normalize it at the boundary the
        same way received_at is. Naive → UTC and aware → CONVERTED to UTC
        (astimezone), so the recap window bound is a '+00:00' ISO string that
        compares chronologically against stored received_at values; a start
        clamped to 'now' if it's implausibly in the future (a bad client clock
        must not open a session that ends before it began or that swallows
        events with a wildly wide window)."""
        if v is None:
            return None
        v = v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
        now = datetime.now(UTC)
        return now if v > now + timedelta(minutes=5) else v


class StatusResponse(BaseModel):
    state: AvailabilityState
    application: str | None
    started_at: datetime | None
