from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


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


class Event(EventIn):
    id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    read_at: datetime | None = None  # view-state only — never affects recaps
