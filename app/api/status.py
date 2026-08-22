from fastapi import APIRouter

from app.models.status import StatusResponse, StatusUpdate
from app.services.status_store import status_store

router = APIRouter()


@router.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    return status_store.get()


@router.post("/status", response_model=StatusResponse)
def set_status(update: StatusUpdate) -> StatusResponse:
    return status_store.set(update)
