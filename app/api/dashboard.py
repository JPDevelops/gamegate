"""The GameGate dashboard (issue #47, pulled forward at Jules' request).

GET /app          — the single-page dashboard. First visit uses ?key=<api
                    token> which is exchanged for an HttpOnly cookie (the
                    link never has to be shared again on that browser).
GET /connections  — real per-connector status: connected / needs setup /
                    disabled — never fake universal connectivity.
GET /digests      — digest history for the Inbox tab.
"""
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import __version__
from app.api.connectors import service_active
from app.config import get_settings
from app.deps import get_digest_repo, get_settings_service
from app.security import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    constant_time_equals,
    issue_session_cookie,
    require_api_token,
    verify_session_cookie,
)
from app.services.digest_service import render_text
from app.services.repositories import DigestRepository
from app.services.settings_service import SettingsService

router = APIRouter()

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"

LOGIN_PAGE = """<!doctype html><meta charset="utf-8">
<body style="background:#0f1014;color:#e8e9ee;font-family:'Segoe UI',sans-serif;
             display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center;max-width:420px">
  <h1 style="letter-spacing:.18em;font-size:18px">GAMEGATE</h1>
  <p style="color:#9a9db0;margin:10px 0 0">This dashboard needs your access link
  (the one with <code>?key=…</code>). Open it from your DM — the link logs this
  browser in once and remembers it.</p>
</div></body>"""


@router.get("/app")
def dashboard(
    request: Request,
    key: str = "",
    gamegate_token: Annotated[str | None, Cookie()] = None,
) -> HTMLResponse:
    expected = get_settings().api_token
    logged_in = verify_session_cookie(gamegate_token, expected) if expected else False
    key_ok = bool(expected) and constant_time_equals(key, expected)
    if key_ok and not logged_in:
        # Exchange the one-time link for a signed session cookie (never the raw
        # token) and drop the key from the URL.
        response = RedirectResponse("/app", status_code=303)
        # Secure when the dashboard is reached over HTTPS (nginx sets
        # X-Forwarded-Proto); stays unset for local HTTP clients so nothing
        # breaks in a plain-HTTP lab.
        over_https = request.headers.get("x-forwarded-proto", "").lower() == "https" \
            or request.url.scheme == "https"
        response.set_cookie(
            COOKIE_NAME, issue_session_cookie(expected), httponly=True,
            secure=over_https, samesite="lax", max_age=SESSION_TTL_SECONDS,
        )
        return response
    if expected and not logged_in and not key_ok:
        return HTMLResponse(LOGIN_PAGE, status_code=401)
    # Fill the version placeholder from the package version so the sidebar can
    # never drift from pyproject again (the old hardcoded "v0.1" outlived 0.2.0).
    html = TEMPLATE.read_text(encoding="utf-8").replace("%%GAMEGATE_VERSION%%", __version__)
    return HTMLResponse(html)


@router.post("/logout")
def logout() -> RedirectResponse:
    """Clear the session cookie for this browser."""
    response = RedirectResponse("/app", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/digests", dependencies=[Depends(require_api_token)])
def digest_history(
    repo: Annotated[DigestRepository, Depends(get_digest_repo)], limit: int = 10
) -> list[dict]:
    digests = repo.recent(min(max(limit, 1), 50))
    for digest in digests:
        digest["text"] = render_text(digest)
    return digests


@router.get("/connections", dependencies=[Depends(require_api_token)])
def connections(
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> dict:
    settings = get_settings()
    prefs = settings_service.get_all()
    gmail_token = Path(os.environ.get("GMAIL_TOKEN_PATH", "token.json"))
    gmail_client = bool(os.environ.get("GMAIL_OAUTH_CLIENT_ID"))
    gmail_enabled = os.environ.get("GMAIL_ENABLED", "").lower() == "true"

    gmail_running = service_active("gmail")
    if gmail_enabled and gmail_token.exists() and gmail_running is not False:
        gmail = {"state": "connected", "detail": "Read-only access, polling your inbox",
                 "can_disconnect": True}
    elif gmail_token.exists() and gmail_client:
        gmail = {"state": "disconnected", "detail": "Authorized but paused",
                 "can_connect": True}
    elif gmail_client:
        gmail = {
            "state": "needs setup",
            "detail": "OAuth client ready — authorize your inbox",
            "action": {"label": "Connect Gmail", "href": "/connect/gmail"},
        }
    else:
        gmail = {"state": "disabled", "detail": "No OAuth client configured"}

    discord_ready = bool(os.environ.get("DISCORD_BOT_TOKEN")) and bool(
        (os.environ.get("GAMEGATE_DISCORD_GUILD_ID", "") or "0").strip().isdigit()
        and int(os.environ.get("GAMEGATE_DISCORD_GUILD_ID", "0"))
    )  # guard: a non-numeric guild id must not 500 the dashboard (N9)
    discord_running = service_active("discord")
    if discord_ready and discord_running is not False:
        discord = {"state": "connected", "detail": "Bot in your server, ingesting messages",
                   "can_disconnect": True}
    elif discord_ready:
        discord = {"state": "disconnected", "detail": "Configured but paused",
                   "can_connect": True}
    else:
        discord = {"state": "needs setup", "detail": "Bot token or server id missing"}

    slack = (
        {"state": "connected", "detail": "Socket Mode"}
        if os.environ.get("SLACK_ENABLED", "").lower() == "true"
        else {"state": "disabled", "detail": "Skipped for v0.1 (product decision)"}
    )

    classifier = (
        {"state": "connected",
         "detail": f"Model: {os.environ.get('CLASSIFIER_MODEL', 'gpt-5-mini')} — "
                   "deterministic fallback always on",
         "can_disconnect": True}
        if os.environ.get("CLASSIFIER_ENABLED", "").lower() == "true"
        else {"state": "disconnected", "detail": "Deterministic rules only",
              "can_connect": True}
    )

    return {
        "discord": discord,
        "gmail": gmail,
        "slack": slack,
        "classifier": classifier,
        "catalog": [
            {"id": "discord", "name": "Discord", "desc": "Messages from your server"},
            {"id": "gmail", "name": "Gmail", "desc": "Read-only inbox monitoring"},
            {"id": "slack", "name": "Slack", "desc": "Coming in a later version"},
            {"id": "classifier", "name": "AI classifier",
             "desc": "Smart prioritization (with fallback)"},
        ],
        "settings": {
            "Version": __version__,
            "Environment": settings.env,
            # Read from the DB settings the routing engine actually uses — not
            # env defaults or hardcoded strings (they drifted from reality).
            "Urgent break-through while gaming": "on" if prefs["urgent_breakthrough"] else "off",
            "VIP senders (→ urgent)": ", ".join(prefs["vip_senders"]) or "none configured",
            "Notification freshness window": f"{prefs['freshness_minutes']} minutes",
        },
    }
