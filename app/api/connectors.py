"""Connector lifecycle — Jules' interactive Connectors tab.

POST /connectors/{name}/disconnect and /connect manage the actual moving
parts per connector (systemd units, token files, env flags). Lab-grade by
design: the API host owns its own services (sudo systemctl is passwordless
for this user), documented in SECURITY_DISPOSITIONS.
"""
import logging
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.security import require_api_token

log = logging.getLogger("gamegate.connectors")

router = APIRouter(dependencies=[Depends(require_api_token)])

ENV_PATH = Path(os.environ.get("GAMEGATE_ENV_FILE", ".env"))

SERVICES = {"discord": "gamegate-discord", "gmail": "gamegate-gmail"}


def service_active(name: str) -> bool | None:
    """True/False from systemd, None when systemd isn't available (tests/CI)."""
    unit = SERVICES.get(name)
    if not unit:
        return None
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5, check=False
        )
        return result.stdout.strip() == "active"
    except (OSError, subprocess.TimeoutExpired):
        return None


def _systemctl(action: str, unit: str) -> None:
    try:
        subprocess.run(["sudo", "systemctl", action, unit], timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("systemctl %s %s failed: %s", action, unit, exc)


def update_env_var(key: str, value: str) -> None:
    """Persist a config flag into the .env file (and this process' env)."""
    os.environ[key] = value
    if not ENV_PATH.exists():
        return
    lines = ENV_PATH.read_text().splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            replaced = True
    if not replaced:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


@router.post("/connectors/{name}/disconnect")
def disconnect(name: str) -> dict:
    if name == "gmail":
        token = Path(os.environ.get("GMAIL_TOKEN_PATH", "token.json"))
        token.unlink(missing_ok=True)
        update_env_var("GMAIL_ENABLED", "false")
        _systemctl("stop", SERVICES["gmail"])
        return {"disconnected": "gmail"}
    if name == "discord":
        _systemctl("stop", SERVICES["discord"])
        return {"disconnected": "discord"}
    if name == "classifier":
        update_env_var("CLASSIFIER_ENABLED", "false")
        return {"disconnected": "classifier"}
    raise HTTPException(status_code=404, detail=f"Unknown connector {name!r}")


@router.post("/connectors/{name}/connect")
def connect(name: str) -> dict:
    if name == "gmail":
        token = Path(os.environ.get("GMAIL_TOKEN_PATH", "token.json"))
        if not token.exists():
            # Authorization needed first — the UI sends the browser there.
            return {"authorize": "/connect/gmail"}
        update_env_var("GMAIL_ENABLED", "true")
        _systemctl("start", SERVICES["gmail"])
        return {"connected": "gmail"}
    if name == "discord":
        _systemctl("start", SERVICES["discord"])
        return {"connected": "discord"}
    if name == "classifier":
        update_env_var("CLASSIFIER_ENABLED", "true")
        return {"connected": "classifier"}
    if name == "slack":
        raise HTTPException(
            status_code=409,
            detail="Slack ships in a later version (product decision for v0.1)",
        )
    raise HTTPException(status_code=404, detail=f"Unknown connector {name!r}")
