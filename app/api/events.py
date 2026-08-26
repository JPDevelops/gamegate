from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.deps import get_event_repo, get_ingest_service, get_settings_service
from app.models.event import Event, EventIn
from app.security import require_api_token
from app.services.ingest_service import IngestService, message_identity
from app.services.repositories import EventRepository
from app.services.settings_service import SettingsService

router = APIRouter(dependencies=[Depends(require_api_token)])

EventRepoDep = Annotated[EventRepository, Depends(get_event_repo)]
IngestDep = Annotated[IngestService, Depends(get_ingest_service)]
SettingsDep = Annotated[SettingsService, Depends(get_settings_service)]


@router.post("/events", response_model=Event, status_code=201)
def create_event(incoming: EventIn, response: Response, ingest: IngestDep) -> Event:
    event, created, _decision = ingest.ingest(incoming)
    if not created:
        # Idempotent replay: same (source, external_id) → return the original.
        response.status_code = 200
    return event


@router.post("/events/{event_id}/read")
def mark_read(event_id: str, repo: EventRepoDep) -> dict:
    if not repo.mark_read(event_id, read=True):
        raise HTTPException(status_code=404, detail="Unknown event")
    return {"read": event_id}


@router.post("/events/{event_id}/unread")
def mark_unread(event_id: str, repo: EventRepoDep) -> dict:
    if not repo.mark_read(event_id, read=False):
        raise HTTPException(status_code=404, detail="Unknown event")
    return {"unread": event_id}


class MarkUrgent(BaseModel):
    urgent: bool


@router.post("/events/{event_id}/mark")
def mark_urgent(
    event_id: str, body: MarkUrgent, repo: EventRepoDep, settings: SettingsDep
) -> dict:
    """Per-message 'Urgent' / 'Not urgent' mark. Re-prioritizes THIS message and
    teaches GameGate the sender's preference (stored locally): urgent → VIP
    (always breaks through); not-urgent → never-urgent list (always held). Both
    override the AI's guess, so future messages from that sender follow the rule."""
    event = repo.find_by_id(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Unknown event")
    who = message_identity(event.source.value, event.sender, event.title)
    if body.urgent:
        repo.set_priority(event_id, "urgent", requires_action=True)
        settings.toggle_sender("vip_senders", who, present=True)
        settings.toggle_sender("never_urgent_senders", who, present=False)
    else:
        repo.set_priority(event_id, "informational", requires_action=False)
        settings.toggle_sender("never_urgent_senders", who, present=True)
        settings.toggle_sender("vip_senders", who, present=False)
    return {"id": event_id, "urgent": body.urgent, "learned": who}


class Silence(BaseModel):
    silenced: bool


@router.post("/events/{event_id}/silence")
def silence_source(
    event_id: str, body: Silence, repo: EventRepoDep, settings: SettingsDep
) -> dict:
    """Per-message 'Silence' (bell): stop THIS message's app/source from ever
    popping an on-screen overlay again. It's still captured (kept in the inbox +
    recap) — just never interrupts. Toggles the source in `muted_sources`;
    silenced=False un-silences. Returns the app label so the UI can say
    'Silenced Blink'."""
    event = repo.find_by_id(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Unknown event")
    who = message_identity(event.source.value, event.sender, event.title)
    settings.toggle_sender("muted_sources", who, present=body.silenced)
    return {"id": event_id, "silenced": body.silenced, "app": who}


@router.post("/events/read-all")
def read_all(repo: EventRepoDep) -> dict:
    return {"marked": repo.mark_all_read()}


class UnreadBulk(BaseModel):
    ids: list[str] = Field(max_length=500)  # cap the bulk op (N5)


@router.post("/events/unread")
def unread_bulk(body: UnreadBulk, repo: EventRepoDep) -> dict:
    count = sum(1 for event_id in body.ids if repo.mark_read(event_id, read=False))
    return {"unmarked": count}


@router.get("/events", response_model=list[Event])
def list_events(
    repo: EventRepoDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[Event]:
    return repo.recent(limit)
