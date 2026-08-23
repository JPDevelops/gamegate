from datetime import datetime
from enum import Enum

from pydantic import BaseModel


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


class StatusResponse(BaseModel):
    state: AvailabilityState
    application: str | None
    started_at: datetime | None
