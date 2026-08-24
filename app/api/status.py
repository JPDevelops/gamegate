from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.deps import get_status_service
from app.models.status import DndUpdate, StatusResponse, StatusUpdate
from app.security import require_api_token
from app.services.agent_status import set_agent_update
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


@router.post("/status/dnd", response_model=StatusResponse)
def set_dnd(update: DndUpdate, service: StatusServiceDep) -> StatusResponse:
    """Dashboard 'Do Not Disturb' — a manual override the detector can't
    overwrite. Distinct from POST /status so a detector poll never clears it."""
    result, _closed_session = service.set_dnd(update.enabled)
    return result


class AgentUpdateReport(BaseModel):
    pending: int
    build: str = ""


@router.post("/agent/update-status")
def report_agent_update(report: AgentUpdateReport) -> dict:
    """The desktop tray reports how many updates are pending, so the dashboard
    Settings area can show the same 'Latest version' / 'Update available' state
    as the tray menu (review request)."""
    set_agent_update(report.pending, report.build)
    return {"recorded": True}
