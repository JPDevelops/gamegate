"""Settings endpoints + the transactional clear-data action."""
import contextlib
import os
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_database
from app.deps import get_settings_service
from app.integrations.gmail_imap import clean_password, verify_imap_login
from app.integrations.gmail_imap import configure as gmail_configure
from app.security import require_api_token
from app.services.classifier import reset_classifier_cache, verify_openai_key
from app.services.settings_service import SettingsService

router = APIRouter(dependencies=[Depends(require_api_token)])

# get_settings_service lives in app.deps (single definition, N2).
SettingsDep = Annotated[SettingsService, Depends(get_settings_service)]


class ClassifierConfig(BaseModel):
    enabled: bool
    api_key: str | None = None  # omit to keep the existing key; "" clears it


class SteamGridConfig(BaseModel):
    api_key: str | None = None  # "" clears it; None is a no-op


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
    note = ""
    if config.api_key is not None:
        key = config.api_key.strip()
        if key:
            # Verify the key with OpenAI before saving, so a bad key is caught
            # here (not silently "connected" then failing on every notification).
            ok, note = verify_openai_key(key)
            if not ok:
                raise HTTPException(status_code=400, detail=note)
            service.set_classifier_key(key)
            os.environ["OPENAI_API_KEY"] = key
        else:
            service.set_classifier_key("")
            os.environ.pop("OPENAI_API_KEY", None)
    service.update({"classifier_enabled": config.enabled})
    os.environ["CLASSIFIER_ENABLED"] = "true" if config.enabled else "false"
    reset_classifier_cache()  # rebuild with the new key/flag
    settings = service.get_all()
    return {
        "enabled": settings["classifier_enabled"],
        "api_key_set": settings["classifier_api_key_set"],
        "note": note,
    }


@router.post("/settings/steamgriddb")
def set_steamgriddb(config: SteamGridConfig, service: SettingsDep) -> dict:
    """Set (or clear) the SteamGridDB API key that powers game-art lookups for
    non-Steam titles (Minecraft, emulators, itch games, …). Verified before
    saving so a bad key is caught here, not silently. Stored locally, never
    returned; applied to os.environ immediately so /art picks it up at once."""
    from app.api.art import verify_steamgriddb_key
    note = ""
    if config.api_key is not None:
        key = config.api_key.strip()
        if key:
            ok, note = verify_steamgriddb_key(key)
            if not ok:
                raise HTTPException(status_code=400, detail=note)
            service.set_steamgriddb_key(key)
            os.environ["STEAMGRIDDB_API_KEY"] = key
        else:
            service.set_steamgriddb_key("")
            os.environ.pop("STEAMGRIDDB_API_KEY", None)
    return {"api_key_set": service.get_all()["steamgriddb_api_key_set"], "note": note}


class GmailConfig(BaseModel):
    enabled: bool
    address: str | None = None      # the user's Gmail address
    app_password: str | None = None  # omit to keep existing; "" clears the creds


@router.post("/settings/gmail")
def set_gmail(config: GmailConfig, service: SettingsDep) -> dict:
    """Connect/disconnect the local Gmail (IMAP + app password) path. The app
    password is validated with a real IMAP login before it's saved — a bad
    address/password is rejected here (400), not silently on every poll — then
    stored locally (never returned) and the background poller is (re)started."""
    note = ""
    cur_addr, cur_pw = service.get_gmail_credentials()
    address = (config.address if config.address is not None else cur_addr).strip()

    if config.app_password is not None:
        if config.app_password.strip():
            if not address:
                raise HTTPException(status_code=400, detail="Enter your Gmail address.")
            ok, note = verify_imap_login(address, config.app_password)
            if not ok:
                raise HTTPException(status_code=400, detail=note)
            service.set_gmail_credentials(address, clean_password(config.app_password))
        else:  # explicit clear
            service.set_gmail_credentials("", "")
    elif config.address is not None and address != cur_addr:
        # Address changed but password kept — re-validate with the stored one.
        if cur_pw:
            ok, note = verify_imap_login(address, cur_pw)
            if not ok:
                raise HTTPException(status_code=400, detail=note)
        service.set_gmail_credentials(address, cur_pw)

    # Can't enable without credentials on file.
    addr, pw = service.get_gmail_credentials()
    enabled = config.enabled and bool(addr and pw)
    service.update({"gmail_enabled": enabled})
    # (Re)start or stop the poller to match — best-effort, never 500 the request.
    with contextlib.suppress(Exception):
        gmail_configure(addr, pw, enabled)

    settings = service.get_all()
    return {
        "enabled": settings["gmail_enabled"],
        "address": settings["gmail_address"],
        "app_password_set": settings["gmail_app_password_set"],
        "note": note,
    }


class TextSyncConfig(BaseModel):
    enabled: bool


@router.post("/settings/text-sync")
def set_text_sync(config: TextSyncConfig, service: SettingsDep) -> dict:
    """Mark phone-text sync on/off. There's no credential: texts arrive as
    captured Windows notifications (via Phone Link). This just records that the
    user finished the setup walkthrough, so the connector shows as active."""
    service.update({"text_sync_enabled": config.enabled})
    return {"enabled": service.get_all()["text_sync_enabled"]}


# Phone Link's Microsoft Store product page — used to INSTALL it when missing.
# (Launching ms-phone: on a PC without Phone Link opens the Store on a useless
# "ms-phone" keyword search, which reads as broken — so we send them here instead.)
_PHONE_LINK_STORE = "ms-windows-store://pdp/?ProductId=9NMPJ99VJBWV"


def _startfile(uri: str) -> dict:
    """Open a fixed, non-user-controlled Windows URI. Best-effort + Windows-only."""
    import sys

    if sys.platform != "win32":
        return {"launched": False, "detail": "Only available on Windows."}
    try:
        os.startfile(uri)  # noqa: S606 — fixed URI, not user input
        return {"launched": True}
    except Exception as exc:  # noqa: BLE001 — never 500; the wizard has a manual fallback
        return {"launched": False, "detail": str(exc)[:200]}


@router.get("/system/phone-link-status")
def phone_link_status() -> dict:
    """Is Windows Phone Link installed for this user? The setup walkthrough asks
    first so it never claims the app is present when it isn't (issue: wizard said
    "already on this PC" on a machine without it). Returns installed=None when it
    can't be determined (non-Windows, or the check failed) — the wizard then
    offers a neutral install-or-open path rather than guessing."""
    import subprocess
    import sys

    if sys.platform != "win32":
        return {"installed": None}
    try:
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "if (Get-AppxPackage -Name Microsoft.YourPhone) { 'yes' } else { 'no' }"],
            capture_output=True, text=True, timeout=12, creationflags=no_window, check=False,
        )
        out = (res.stdout or "").strip().lower()
        return {"installed": True} if "yes" in out else (
            {"installed": False} if "no" in out else {"installed": None})
    except Exception:  # noqa: BLE001 — detection is best-effort
        return {"installed": None}


@router.post("/system/open-phone-link")
def open_phone_link() -> dict:
    """Launch Phone Link (ms-phone:) so the walkthrough can open it in one click.
    Windows-only, best-effort — the wizard has a manual fallback."""
    return _startfile("ms-phone:")


@router.post("/system/get-phone-link")
def get_phone_link() -> dict:
    """Open Phone Link's Microsoft Store page so the user can install it when the
    status check says it's missing."""
    return _startfile(_PHONE_LINK_STORE)


class BannerSuppressConfig(BaseModel):
    enabled: bool


@router.post("/settings/notifications-suppress")
def set_banner_suppression(config: BannerSuppressConfig, service: SettingsDep) -> dict:
    """Silence duplicate pop-ups: mute (or restore) the native Windows banner for
    the messaging apps GameGate surfaces, so the user is pinged once — by
    GameGate — not twice. The notifications are STILL captured (only the banner
    is muted). Applied immediately + persisted so it survives restarts. Windows-
    only; a no-op elsewhere."""
    from app.services.notification_banners import apply as apply_banners

    service.update({"suppress_source_banners": config.enabled})
    affected = apply_banners(config.enabled)
    return {"enabled": config.enabled, "affected": affected}


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
