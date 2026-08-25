from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.deps import get_status_service
from app.models.status import DndUpdate, StatusResponse, StatusUpdate
from app.security import require_api_token
from app.services.agent_status import request_apply, set_agent_update, take_apply_request
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
    version: str = ""
    available_version: str = ""  # the tag the user can update TO (when pending)


@router.post("/agent/update-status")
def report_agent_update(report: AgentUpdateReport) -> dict:
    """The desktop tray reports how many updates are pending, so the dashboard
    Settings area can show the same 'Latest version' / 'Update available' state
    as the tray menu (review request)."""
    set_agent_update(report.pending, report.build, report.version, report.available_version)
    return {"recorded": True}


@router.post("/agent/request-update")
def request_agent_apply() -> dict:
    """Dashboard 'Update now' → ask the tray to apply the pending update, so the
    user updates from inside the app (no tray/corner prompt, no restart)."""
    request_apply()
    return {"requested": True}


@router.get("/agent/apply-request")
def poll_agent_apply() -> dict:
    """The tray polls this; returns (and consumes) whether the user asked to
    update from the dashboard."""
    return {"requested": take_apply_request()}
