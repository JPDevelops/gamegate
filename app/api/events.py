from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from app.deps import get_event_repo, get_ingest_service
from app.models.event import Event, EventIn
from app.security import require_api_token
from app.services.ingest_service import IngestService
from app.services.repositories import EventRepository

router = APIRouter(dependencies=[Depends(require_api_token)])

EventRepoDep = Annotated[EventRepository, Depends(get_event_repo)]
IngestDep = Annotated[IngestService, Depends(get_ingest_service)]


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


@router.post("/events/read-all")
def read_all(repo: EventRepoDep) -> dict:
    return {"marked": repo.mark_all_read()}


class UnreadBulk(BaseModel):
    ids: list[str]


@router.post("/events/unread")
def unread_bulk(body: UnreadBulk, repo: EventRepoDep) -> dict:
    count = sum(1 for event_id in body.ids if repo.mark_read(event_id, read=False))
    return {"unmarked": count}


@router.get("/events", response_model=list[Event])
def list_events(
    repo: EventRepoDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[Event]:
    return repo.recent(limit)