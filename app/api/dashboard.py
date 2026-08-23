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

from fastapi import APIRouter, Cookie, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from app import __version__
from app.config import get_settings
from app.deps import get_digest_repo
from app.security import COOKIE_NAME, require_api_token
from app.services.digest_service import render_text
from app.services.repositories import DigestRepository

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
    key: str = "",
    gamegate_token: Annotated[str | None, Cookie()] = None,
) -> HTMLResponse:
    expected = get_settings().api_token
    if expected and key == expected and gamegate_token != expected:
        # Exchange the one-time link for a cookie and drop the key from the URL.
        response = RedirectResponse("/app", status_code=303)
        # secure=False deliberately: lab clients still use HTTP (see
        # docs/SECURITY_DISPOSITIONS.md #26) — the cookie is no more exposed
        # than the header those clients already send. Flip with full TLS.
        response.set_cookie(
            COOKIE_NAME, expected, httponly=True, secure=False, samesite="lax",
            max_age=60 * 60 * 24 * 90,
        )
        return response
    if expected and gamegate_token != expected and key != expected:
        return HTMLResponse(LOGIN_PAGE, status_code=401)
    return HTMLResponse(TEMPLATE.read_text())


@router.get("/digests", dependencies=[Depends(require_api_token)])
def digest_history(
    repo: Annotated[DigestRepository, Depends(get_digest_repo)], limit: int = 10
) -> list[dict]:
    digests = repo.recent(min(max(limit, 1), 50))
    for digest in digests:
        digest["text"] = render_text(digest)
    return digests


@router.get("/connections", dependencies=[Depends(require_api_token)])
def connections() -> dict:
    settings = get_settings()
    gmail_token = Path(os.environ.get("GMAIL_TOKEN_PATH", "token.json"))
    gmail_client = bool(os.environ.get("GMAIL_OAUTH_CLIENT_ID"))
    gmail_enabled = os.environ.get("GMAIL_ENABLED", "").lower() == "true"

    if gmail_enabled and gmail_token.exists():
        gmail = {"state": "connected", "detail": "Read-only access, polling your inbox"}
    elif gmail_client:
        gmail = {
            "state": "needs setup",
            "detail": "OAuth client ready — authorize your inbox",
            "action": {"label": "Connect Gmail", "href": "/connect/gmail"},
        }
    else:
        gmail = {"state": "disabled", "detail": "No OAuth client configured"}

    discord_ready = bool(os.environ.get("DISCORD_BOT_TOKEN")) and bool(
        int(os.environ.get("GAMEGATE_DISCORD_GUILD_ID", "0"))
    )
    discord = (
        {"state": "connected", "detail": "Bot in your server, ingesting messages"}
        if discord_ready
        else {"state": "needs setup", "detail": "Bot token or server id missing"}
    )

    slack = (
        {"state": "connected", "detail": "Socket Mode"}
        if os.environ.get("SLACK_ENABLED", "").lower() == "true"
        else {"state": "disabled", "detail": "Skipped for v0.1 (product decision)"}
    )

    classifier = (
        {"state": "connected", "detail": f"Model: {os.environ.get('CLASSIFIER_MODEL', 'gpt-5-mini')} — deterministic fallback always on"}
        if os.environ.get("CLASSIFIER_ENABLED", "").lower() == "true"
        else {"state": "disabled", "detail": "Deterministic rules only"}
    )

    return {
        "discord": discord,
        "gmail": gmail,
        "slack": slack,
        "classifier": classifier,
        "settings": {
            "Version": __version__,
            "Environment": settings.env,
            "Urgent break-through while gaming": "on" if settings.urgent_breaks_through_gaming else "off",
            "VIP senders (Gmail → urgent)": os.environ.get("GMAIL_VIP_SENDERS") or "none configured",
            "Notification freshness window": "10 minutes",
        },
    }
