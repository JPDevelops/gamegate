from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_digest_repo, get_event_repo, get_notification_repo
from app.security import require_api_token
from app.services.digest_service import build_digest, render_text
from app.services.repositories import (
    DigestRepository,
    EventRepository,
    NotificationRepository,
)

router = APIRouter()

DigestRepoDep = Annotated[DigestRepository, Depends(get_digest_repo)]
EventRepoDep = Annotated[EventRepository, Depends(get_event_repo)]
NotificationRepoDep = Annotated[NotificationRepository, Depends(get_notification_repo)]


@router.get("/digest")
def digest_preview(events: EventRepoDep) -> dict:
    """Preview of what the next digest would contain. Consumes nothing."""
    preview = build_digest(None, events.undelivered())
    preview["text"] = render_text(preview)
    return preview


@router.get("/digest/latest")
def latest_digest(digests: DigestRepoDep) -> dict:
    latest = digests.latest()
    if latest is None:
        raise HTTPException(status_code=404, detail="No digest generated yet")
    latest["text"] = render_text(latest)
    return latest


@router.get("/digests/pending")
def pending_digests(digests: DigestRepoDep) -> list[dict]:
    pending = digests.pending()
    for digest in pending:
        digest["text"] = render_text(digest)
    return pending


@router.post("/digests/{digest_id}/ack", dependencies=[Depends(require_api_token)])
def ack_digest(digest_id: str, digests: DigestRepoDep) -> dict:
    if not digests.ack(digest_id):
        raise HTTPException(status_code=404, detail="Unknown or already-delivered digest")
    return {"acknowledged": digest_id}


@router.get("/notifications/pending")
def pending_notifications(notifications: NotificationRepoDep) -> list[dict]:
    return notifications.pending()


@router.post("/notifications/{notification_id}/ack", dependencies=[Depends(require_api_token)])
def ack_notification(notification_id: str, notifications: NotificationRepoDep) -> dict:
    if not notifications.ack(notification_id):
        raise HTTPException(status_code=404, detail="Unknown or already-delivered notification")
    return {"acknowledged": notification_id}