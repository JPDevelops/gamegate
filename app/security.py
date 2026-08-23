"""API authentication for write operations.

Lab-grade shared-token scheme, per the runbook's Step 14 guidance: the
detector and connectors send X-GameGate-Token; mutating endpoints reject
requests without it. If GAMEGATE_API_TOKEN is unset (local development),
auth is disabled — the deployment docs call this out.
"""
from typing import Annotated

from fastapi import Cookie, Header, HTTPException

from app.config import get_settings

COOKIE_NAME = "gamegate_token"


def require_api_token(
    x_gamegate_token: Annotated[str | None, Header()] = None,
    gamegate_token: Annotated[str | None, Cookie()] = None,
) -> None:
    """Programs send the header; the browser dashboard sends the HttpOnly
    cookie set by /app?key=... — either must match."""
    expected = get_settings().api_token
    if expected is None:
        return
    if x_gamegate_token != expected and gamegate_token != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid API token")
