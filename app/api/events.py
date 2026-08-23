from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.deps import get_event_repo
from app.models.event import Event, EventIn
from app.services.repositories import EventRepository

router = APIRouter()

EventRepoDep = Annotated[EventRepository, Depends(get_event_repo)]


@router.post("/events", response_model=Event, status_code=201)
def create_event(incoming: EventIn, response: Response, repo: EventRepoDep) -> Event:
    event, created = repo.add(incoming)
    if not created:
        # Idempotent replay: same (source, external_id) → return the original.
        response.status_code = 200
    return event


@router.get("/events", response_model=list[Event])
def list_events(repo: EventRepoDep, limit: int = 50) -> list[Event]:
    return repo.recent(limit)
