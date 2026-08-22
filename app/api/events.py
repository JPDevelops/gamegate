from fastapi import APIRouter

from app.models.event import Event, EventIn
from app.services.event_store import event_store

router = APIRouter()


@router.post("/events", response_model=Event, status_code=201)
def create_event(incoming: EventIn) -> Event:
    return event_store.add(incoming)


@router.get("/events", response_model=list[Event])
def list_events(limit: int = 50) -> list[Event]:
    return event_store.recent(limit)
