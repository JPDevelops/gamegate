"""Settings endpoints + the transactional clear-data action."""
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import get_database
from app.deps import get_settings_service
from app.security import require_api_token
from app.services.settings_service import SettingsService

router = APIRouter(dependencies=[Depends(require_api_token)])

# get_settings_service lives in app.deps (single definition, N2).
SettingsDep = Annotated[SettingsService, Depends(get_settings_service)]


@router.get("/settings")
def read_settings(service: SettingsDep) -> dict:
    return service.get_all()


@router.put("/settings")
def write_settings(service: SettingsDep, changes: Annotated[dict, Body()]) -> dict:
    try:
        return service.update(changes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/settings/client")
def client_settings(service: SettingsDep) -> dict:
    """The subset the PC app applies (sound, overlay duration) + version so
    it only reacts to actual changes."""
    settings = service.get_all()
    return {
        "notification_sound": settings["notification_sound"],
        "overlay_duration_s": settings["overlay_duration_s"],
        "version": settings["version"],
    }


@router.post("/data/clear")
def clear_data(confirm: Annotated[str, Body(embed=True)] = "") -> dict:
    """Deletes message data (events, digests, sessions, notifications) in one
    transaction. Settings, connections, and art cache survive. Requires the
    typed confirmation string."""
    if confirm != "DELETE":
        raise HTTPException(status_code=422, detail='Type "DELETE" to confirm')
    conn = get_database().connection()
    with conn:
        counts = {}
        for table in ("notifications", "digests", "events", "sessions"):
            counts[table] = conn.execute(f"DELETE FROM {table}").rowcount
        conn.execute("UPDATE status SET state='available', application=NULL, started_at=NULL")
    return {"cleared": counts}
