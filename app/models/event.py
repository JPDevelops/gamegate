from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class EventSource(str, Enum):
    GMAIL = "gmail"
    SLACK = "slack"
    DISCORD = "discord"
    SYSTEM = "system"


class EventPriority(str, Enum):
    URGENT = "urgent"
    ACTIONABLE = "actionable"
    INFORMATIONAL = "informational"
    IGNORE = "ignore"


class EventIn(BaseModel):
    source: EventSource
    external_id: str
    sender: str
    title: str
    content: str = ""
    received_at: datetime
    priority: EventPriority
    requires_action: bool = False
    metadata: dict = Field(default_factory=dict)

    @field_validator("received_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        """Coerce naive timestamps to UTC at the boundary so downstream sorting
        never mixes naive and aware datetimes (which raises TypeError and 500s
        the digest). Normalizes once, on store, instead of ad hoc everywhere."""
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v


class Event(EventIn):
    id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    read_at: datetime | None = None  # view-state only — never affects recaps
