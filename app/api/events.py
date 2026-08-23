from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

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


@router.get("/events", response_model=list[Event])
def list_events(
    repo: EventRepoDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[Event]:
    return repo.recent(limit)