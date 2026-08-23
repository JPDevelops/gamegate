from typing import Annotated

from fastapi import APIRouter, Depends

from app.deps import get_status_service
from app.models.status import StatusResponse, StatusUpdate
from app.security import require_api_token
from app.services.status_service import StatusService

router = APIRouter(dependencies=[Depends(require_api_token)])

StatusServiceDep = Annotated[StatusService, Depends(get_status_service)]


@router.get("/status", response_model=StatusResponse)
def get_status(service: StatusServiceDep) -> StatusResponse:
    return service.get()


@router.post("/status", response_model=StatusResponse)
def set_status(update: StatusUpdate, service: StatusServiceDep) -> StatusResponse:
    result, _closed_session = service.set(update)
    return result