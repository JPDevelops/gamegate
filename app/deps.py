from app.config import get_settings
from app.db import get_database
from app.services.ingest_service import IngestService
from app.services.repositories import (
    ConnectorHealthRepository,
    DigestRepository,
    EventRepository,
    NotificationRepository,
    SessionRepository,
    StatusRepository,
)
from app.services.settings_service import SettingsService
from app.services.status_service import StatusService


def get_event_repo() -> EventRepository:
    return EventRepository(get_database())


def get_settings_service() -> SettingsService:
    return SettingsService(get_database())


def get_status_service() -> StatusService:
    db = get_database()
    return StatusService(
        StatusRepository(db),
        SessionRepository(db),
        EventRepository(db),
        DigestRepository(db),
    )


def get_ingest_service() -> IngestService:
    return IngestService(get_database(), get_settings())


def get_digest_repo() -> DigestRepository:
    return DigestRepository(get_database())


def get_notification_repo() -> NotificationRepository:
    return NotificationRepository(get_database())


def get_connector_health_repo() -> ConnectorHealthRepository:
    return ConnectorHealthRepository(get_database())
