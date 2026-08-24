"""The 'Connect Gmail' flow — the first slice of the v0.2 Connections GUI,
pulled forward on Jules' product decision (2026-08-23).

GET /connect/gmail?key=<api token>   → redirects the browser to Google consent
GET /oauth/gmail/callback            → Google returns here; we exchange the
                                       code and store token.json server-side

Notes:
- These two endpoints live OUTSIDE the token-header auth (a browser can't
  send X-GameGate-Token). /connect/gmail instead requires the api token as a
  query parameter; the callback is protected by the single-use random state.
- Google requires an HTTPS redirect URI — served via the sslip.io TLS host.
- Only the readonly scope is ever requested.
"""
import json
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import get_settings

log = logging.getLogger("gamegate.gmail.oauth")

router = APIRouter()

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
STATE_TTL_SECONDS = 600

_pending_states: dict[str, float] = {}


def _oauth_config() -> tuple[str, str, str]:
    client_id = os.environ.get("GMAIL_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GMAIL_OAUTH_CLIENT_SECRET", "")
    redirect_uri = os.environ.get(
        "GMAIL_REDIRECT_URI", "https://YOUR-SERVER/oauth/gmail/callback"
    )
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail="Gmail OAuth client not configured "
            "(GMAIL_OAUTH_CLIENT_ID / GMAIL_OAUTH_CLIENT_SECRET)",
        )
    return client_id, client_secret, redirect_uri


def _issue_state() -> str:
    now = time.time()
    for state, expiry in list(_pending_states.items()):
        if expiry < now:
            del _pending_states[state]
    state = secrets.token_urlsafe(24)
    _pending_states[state] = now + STATE_TTL_SECONDS
    return state


def exchange_code(
    code: str, client_id: str, client_secret: str, redirect_uri: str,
    http: httpx.Client | None = None,
) -> dict:
    """Exchange the authorization code for tokens. Raises HTTPException on
    any provider failure — the browser sees a clear error, nothing is stored."""
    client = http or httpx.Client(timeout=15)
    owns_client = http is None  # close only the client we created (N31)
    try:
        response = client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        log.warning("Code exchange failed: %s", exc)
        raise HTTPException(status_code=502, detail="Google token exchange failed") from exc
    finally:
        if owns_client:
            client.close()


def write_token_file(tokens: dict, client_id: str, client_secret: str) -> str:
    """Persist google-auth's authorized-user format, chmod 600."""
    token_path = os.environ.get("GMAIL_TOKEN_PATH", "token.json")
    payload = {
        "type": "authorized_user",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tokens.get("refresh_token", ""),
        "token": tokens.get("access_token", ""),
        "scopes": [SCOPE],
        "token_uri": TOKEN_URL,
    }
    path = Path(token_path)
    # Create with 0600 from the start (O_CREAT|O_WRONLY|O_TRUNC) rather than
    # write-then-chmod, so the refresh token is never briefly world-readable at
    # the process umask (N36).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(payload))
    path.chmod(0o600)  # tighten even if the file already existed with looser perms
    return str(path)


@router.get("/connect/gmail")
def connect_gmail(
    key: str = "", gamegate_token: str | None = Cookie(default=None)
) -> RedirectResponse:
    expected = get_settings().api_token
    def _eq(a, b):
        return secrets.compare_digest((a or '').encode('utf-8', 'ignore'), (b or '').encode())
    if expected and not _eq(key, expected) and not _eq(gamegate_token, expected):
        raise HTTPException(status_code=401, detail="key query parameter required")
    client_id, _secret, redirect_uri = _oauth_config()
    params = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",   # we need a refresh token
            "prompt": "consent",
            "state": _issue_state(),
        }
    )
    return RedirectResponse(f"{AUTH_URL}?{params}", status_code=307)


@router.get("/oauth/gmail/callback")
def gmail_callback(code: str = "", state: str = "", error: str = "") -> HTMLResponse:
    if error:
        raise HTTPException(status_code=400, detail=f"Google reported: {error}")
    if not code or state not in _pending_states:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    del _pending_states[state]  # single use

    client_id, client_secret, redirect_uri = _oauth_config()
    tokens = exchange_code(code, client_id, client_secret, redirect_uri)
    path = write_token_file(tokens, client_id, client_secret)
    log.info("Gmail connected; token stored at %s", path)
    return HTMLResponse(
        """<!doctype html><meta charset="utf-8">
        <body style="background:#0f1014;color:#e8e9ee;font-family:'Segoe UI',sans-serif;
                     display:grid;place-items:center;height:100vh;margin:0">
        <div style="text-align:center">
          <div style="font-size:44px">✅</div>
          <h1 style="margin:12px 0 6px">Gmail connected</h1>
          <p style="color:#9a9db0">GameGate has read-only access. You can close this tab.</p>
        </div></body>"""
    )
