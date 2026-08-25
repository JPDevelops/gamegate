"""Settings endpoints + the transactional clear-data action."""
import os
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_database
from app.deps import get_settings_service
from app.security import require_api_token
from app.services.classifier import reset_classifier_cache
from app.services.settings_service import SettingsService

router = APIRouter(dependencies=[Depends(require_api_token)])

# get_settings_service lives in app.deps (single definition, N2).
SettingsDep = Annotated[SettingsService, Depends(get_settings_service)]


class ClassifierConfig(BaseModel):
    enabled: bool
    api_key: str | None = None  # omit to keep the existing key; "" clears it


@router.get("/settings")
def read_settings(service: SettingsDep) -> dict:
    return service.get_all()


@router.put("/settings")
def write_settings(service: SettingsDep, changes: Annotated[dict, Body()]) -> dict:
    try:
        return service.update(changes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/settings/classifier")
def set_classifier(config: ClassifierConfig, service: SettingsDep) -> dict:
    """Set the AI classifier on/off and (optionally) its API key. The key is
    stored locally and never returned. api_key=None keeps the existing key;
    api_key="" clears it. Applies to os.environ immediately so the running
    classifier picks it up (via the reset below)."""
    if config.api_key is not None:
        key = config.api_key.strip()
        service.set_classifier_key(key)
        if key:
            os.environ["OPENAI_API_KEY"] = key
        else:
            os.environ.pop("OPENAI_API_KEY", None)
    service.update({"classifier_enabled": config.enabled})
    os.environ["CLASSIFIER_ENABLED"] = "true" if config.enabled else "false"
    reset_classifier_cache()  # rebuild with the new key/flag
    settings = service.get_all()
    return {
        "enabled": settings["classifier_enabled"],
        "api_key_set": settings["classifier_api_key_set"],
    }


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
        # Reset override_state too (review NITPICK): a data wipe while DND is on
        # must not leave the status pinned to the manual override.
        conn.execute(
            "UPDATE status SET state='available', application=NULL, started_at=NULL,"
            " override_state=NULL"
        )
    return {"cleared": counts}
