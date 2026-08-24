from datetime import UTC, datetime, timedelta
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
    external_id: str = Field(max_length=512)
    sender: str = Field(max_length=512)
    title: str = Field(max_length=1024)
    content: str = Field(default="", max_length=8192)
    received_at: datetime
    priority: EventPriority
    requires_action: bool = False
    metadata: dict = Field(default_factory=dict)

    @field_validator("received_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        """Normalize every timestamp to UTC — naive ones get UTC attached, aware
        ones are CONVERTED (astimezone), not just relabeled — so the stored
        isoformat is always '+00:00'. The recap window query compares received_at
        as ISO strings in SQLite, which only orders chronologically when every
        value shares the UTC offset; a preserved '-07:00' would sort by its wall
        clock, not its instant, and land in the wrong recap. Also clamp a future
        timestamp to now — a future date would never be 'stale' and would defeat
        the freshness gate, turning every held message into an interruption."""
        v = v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
        now = datetime.now(UTC)
        return now if v > now + timedelta(minutes=5) else v

    @field_validator("metadata")
    @classmethod
    def _bounded_metadata(cls, v: dict) -> dict:
        """Cap the serialized metadata so an unbounded blob can't be stored (M11)."""
        import json

        if len(json.dumps(v)) > 8192:
            raise ValueError("metadata too large (max 8KB serialized)")
        return v


class Event(EventIn):
    id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    read_at: datetime | None = None  # view-state only — never affects recaps
