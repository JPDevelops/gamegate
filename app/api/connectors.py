"""Connector lifecycle — Jules' interactive Connectors tab.

Connect/disconnect just flip a per-connector ENABLED flag in the .env file; the
connector processes (which run continuously under systemd) read that flag live
and start/stop their own work. The web API therefore NEVER shells out to `sudo`
— an app-level compromise can't restart services or gain root (review B2).
"""
import json
import logging
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.security import require_api_token

log = logging.getLogger("gamegate.connectors")

router = APIRouter(dependencies=[Depends(require_api_token)])

ENV_PATH = Path(os.environ.get("GAMEGATE_ENV_FILE", ".env"))

# The env flag each connector self-gates on.
ENABLED_FLAG = {"gmail": "GMAIL_ENABLED", "discord": "GAMEGATE_DISCORD_ENABLED"}


def read_env_flag(key: str) -> str | None:
    """Read a key's CURRENT value from the .env file (not this process' cached
    os.environ) so the connector processes see a toggle the API just wrote."""
    if not ENV_PATH.exists():
        return os.environ.get(key)
    for line in ENV_PATH.read_text().splitlines():
        s = line.strip()
        if s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() == key:
            return v.strip().strip("'\"")
    return os.environ.get(key)


def connector_enabled(name: str) -> bool:
    """Whether a connector is switched on (its live .env flag is 'true')."""
    key = ENABLED_FLAG.get(name)
    return bool(key) and (read_env_flag(key) or "").lower() == "true"


def service_active(name: str) -> bool | None:
    """A connector is 'connected' when its enable flag is on — the unit runs
    continuously and self-gates, so no `systemctl is-active` subprocess is
    needed (removes the per-poll fork, M10, and the sudo dependency, B2)."""
    if name not in ENABLED_FLAG:
        return None
    return connector_enabled(name)


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
        update_env_var("GMAIL_ENABLED", "false")  # the poller stops on next cycle
        return {"disconnected": "gmail", "grant_revoked": revoked}
    if name == "discord":
        update_env_var("GAMEGATE_DISCORD_ENABLED", "false")  # bot stops ingesting
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
        update_env_var("GMAIL_ENABLED", "true")  # the running poller picks it up
        return {"connected": "gmail"}
    if name == "discord":
        update_env_var("GAMEGATE_DISCORD_ENABLED", "true")  # bot resumes ingesting
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
