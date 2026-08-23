from app.db import get_database
from app.services.repositories import (
    DigestRepository,
    EventRepository,
    SessionRepository,
    StatusRepository,
)
from app.services.status_service import StatusService


def get_event_repo() -> EventRepository:
    return EventRepository(get_database())


def get_status_service() -> StatusService:
    db = get_database()
    return StatusService(StatusRepository(db), SessionRepository(db))


def get_session_repo() -> SessionRepository:
    return SessionRepository(get_database())


def get_digest_repo() -> DigestRepository:
    return DigestRepository(get_database())
