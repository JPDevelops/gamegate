"""Connector lifecycle — Jules' interactive Connectors tab.

POST /connectors/{name}/disconnect and /connect manage the actual moving
parts per connector (systemd units, token files, env flags). Lab-grade by
design: the API host owns its own services (sudo systemctl is passwordless
for this user), documented in SECURITY_DISPOSITIONS.
"""
import json
import logging
import os
import subprocess
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.security import require_api_token

log = logging.getLogger("gamegate.connectors")

router = APIRouter(dependencies=[Depends(require_api_token)])

ENV_PATH = Path(os.environ.get("GAMEGATE_ENV_FILE", ".env"))

SERVICES = {"discord": "gamegate-discord", "gmail": "gamegate-gmail"}


_active_cache: dict[str, tuple[float, bool | None]] = {}
_ACTIVE_TTL = 8.0  # seconds — the dashboard polls /connections every 15s


def service_active(name: str) -> bool | None:
    """True/False from systemd, None when systemd isn't available (tests/CI).
    Cached for a few seconds so the dashboard's 15s poll doesn't fork a
    subprocess per connector on every request (M10)."""
    unit = SERVICES.get(name)
    if not unit:
        return None
    now = time.monotonic()
    cached = _active_cache.get(name)
    if cached and now - cached[0] < _ACTIVE_TTL:
        return cached[1]
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit], capture_output=True, text=True,
            timeout=5, check=False,
        )
        value: bool | None = result.stdout.strip() == "active"
    except (OSError, subprocess.TimeoutExpired):
        value = None
    _active_cache[name] = (now, value)
    return value


def _systemctl(action: str, unit: str) -> bool | None:
    """Run `systemctl <action> <unit>`. Returns True on success, False when it
    ran but exited non-zero, None when systemd isn't reachable at all (tests/CI).
    Callers surface False as an error rather than reporting a fake success (M13)."""
    try:
        result = subprocess.run(
            ["sudo", "systemctl", action, unit], timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("systemctl %s %s could not run: %s", action, unit, exc)
        return None
    if result.returncode != 0:
        log.warning("systemctl %s %s exited %s", action, unit, result.returncode)
        return False
    return True


def _require_systemctl_ok(action: str, unit: str) -> None:
    """Raise 502 when systemctl ran and failed; tolerate 'not available'."""
    if _systemctl(action, unit) is False:
        raise HTTPException(
            status_code=502, detail=f"Failed to {action} {unit}; check service logs"
        )


def _is_active_assignment(line: str, key: str) -> bool:
    """True only for an ACTIVE `KEY=...` line — not a commented `# KEY=...`.
    We must not silently uncomment a deliberately-disabled variable (M12)."""
    stripped = line.lstrip()
    return not stripped.startswith("#") and stripped.split("=", 1)[0].strip() == key


def _quote_env_value(value: str) -> str:
    """Single-quote a value that isn't a bare token, so it can't inject extra
    lines/directives into the EnvironmentFile systemd loads (M12)."""
    if value and all(c.isalnum() or c in "._-:/" for c in value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def update_env_var(key: str, value: str) -> None:
    """Persist a config flag into the .env file (and this process' env),
    atomically (write tmp + os.replace) so a crash can't truncate the secrets
    file, and only replacing an active assignment (never uncommenting one)."""
    os.environ[key] = value
    if not ENV_PATH.exists():
        return
    lines = ENV_PATH.read_text().splitlines()
    new_line = f"{key}={_quote_env_value(value)}"
    replaced = False
    for i, line in enumerate(lines):
        if _is_active_assignment(line, key):
            lines[i] = new_line
            replaced = True
    if not replaced:
        lines.append(new_line)
    tmp = ENV_PATH.with_name(ENV_PATH.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)  # explicit 0600 — the secrets file must not depend on umask (M7)
    os.replace(tmp, ENV_PATH)  # atomic swap; never a truncated .env


def _revoke_google_grant(token_path: Path) -> bool:
    """Best-effort: tell Google to revoke the OAuth grant so 'disconnect' truly
    disconnects, instead of only deleting the local token while Google keeps the
    authorization live (M10). Returns True on a confirmed revoke."""
    try:
        data = json.loads(token_path.read_text())
    except (OSError, ValueError):
        return False
    secret = data.get("refresh_token") or data.get("token")
    if not secret:
        return False
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": secret},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code == 200:
            return True
        log.warning("Google token revoke returned %s", resp.status_code)
    except httpx.HTTPError as exc:
        log.warning("Google token revoke failed: %s", exc)
    return False


@router.post("/connectors/{name}/disconnect")
def disconnect(name: str) -> dict:
    if name == "gmail":
        token = Path(os.environ.get("GMAIL_TOKEN_PATH", "token.json"))
        revoked = _revoke_google_grant(token)  # revoke BEFORE deleting the token
        token.unlink(missing_ok=True)
        update_env_var("GMAIL_ENABLED", "false")
        _require_systemctl_ok("stop", SERVICES["gmail"])
        return {"disconnected": "gmail", "grant_revoked": revoked}
    if name == "discord":
        _require_systemctl_ok("stop", SERVICES["discord"])
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
        _require_systemctl_ok("start", SERVICES["gmail"])
        return {"connected": "gmail"}
    if name == "discord":
        _require_systemctl_ok("start", SERVICES["discord"])
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
