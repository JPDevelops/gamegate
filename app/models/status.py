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


class StatusResponse(BaseModel):
    state: AvailabilityState
    application: str | None
    started_at: datetime | None
