"""API authentication for write operations.

Lab-grade shared-token scheme, per the runbook's Step 14 guidance: the
detector and connectors send X-GameGate-Token; mutating endpoints reject
requests without it. If GAMEGATE_API_TOKEN is unset, auth is disabled — but
the app now refuses to *start* in that state unless GAMEGATE_ENV is explicitly
"development" (see app/main.py), so an unset token fails closed in production.

Browsers don't *store* the master token: the first `/app?key=<token>` visit
immediately exchanges it for a signed, expiring **session cookie**
(issue_session_cookie) and 303-redirects, so what persists is a time-boxed
credential that is not the master token, and rotating GAMEGATE_API_TOKEN
invalidates every outstanding cookie at once. The desktop app keeps the token
out of URLs entirely: it mints a single-use, short-TTL **login ticket** over the
authenticated header (POST /auth/ticket) and opens `/app?ticket=…`. The
shareable DM `?key=…` link still carries the token once for that request (server
logs are scrubbed — nginx logs `$uri`, uvicorn runs `--no-access-log`), so the
ticket path is preferred for anything programmatic.
"""
import hashlib
import hmac
import secrets
import time
from typing import Annotated

from fastapi import Cookie, Header, HTTPException

from app.config import get_settings

COOKIE_NAME = "gamegate_token"
# 14 days, not 90: the cookie is a stateless bearer with no server-side
# revocation (logout only clears the browser copy), so a shorter life bounds the
# window a leaked cookie stays valid (review: 90-day session too long). The
# desktop app mints a fresh login ticket every time it opens the dashboard, so a
# shorter TTL costs the owner nothing in practice. Rotating GAMEGATE_API_TOKEN
# still invalidates every outstanding cookie immediately.
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14  # 14 days


def constant_time_equals(a: str | None, b: str | None) -> bool:
    """Byte-wise constant-time comparison. Encoding as bytes (rather than
    passing str) avoids compare_digest raising on a non-ASCII input — a
    non-ASCII token would otherwise 500 instead of cleanly failing to match."""
    return secrets.compare_digest(
        (a or "").encode("utf-8", "ignore"), (b or "").encode("utf-8", "ignore")
    )


def issue_session_cookie(secret: str, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    """Mint a signed session token: "<expiry>.<nonce>.<hmac>". The cookie value
    is derived from — but is not — the master token, so a cookie leak does not
    hand over the credential the detector and connectors authenticate with."""
    expiry = int(time.time()) + ttl_seconds
    nonce = secrets.token_urlsafe(16)
    return f"{expiry}.{nonce}.{_sign(secret, expiry, nonce)}"


def verify_session_cookie(token: str | None, secret: str) -> bool:
    """True iff the cookie is a well-formed, unexpired, correctly-signed token."""
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    expiry_raw, nonce, sig = parts
    try:
        expiry = int(expiry_raw)
    except ValueError:
        return False
    if expiry < time.time():
        return False
    return constant_time_equals(sig, _sign(secret, expiry, nonce))


def _sign(secret: str, expiry: int, nonce: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), f"{expiry}.{nonce}".encode(), hashlib.sha256
    ).hexdigest()


# One-time login tickets: a short-lived, single-use credential DISTINCT from the
# master token. The desktop app (which holds the token) requests a ticket over
# the authenticated header, then opens /app?ticket=<ticket> — so the master token
# never lands in a URL, browser/webview history, or a copied link (review MAJOR).
# In-memory is fine: the API runs a single worker, and a lost ticket just means
# re-opening the dashboard.
LOGIN_TICKET_TTL_SECONDS = 120
_login_tickets: dict[str, float] = {}


def issue_login_ticket() -> str:
    now = time.time()
    for ticket, expiry in list(_login_tickets.items()):
        if expiry < now:
            del _login_tickets[ticket]
    ticket = secrets.token_urlsafe(24)
    _login_tickets[ticket] = now + LOGIN_TICKET_TTL_SECONDS
    return ticket


def consume_login_ticket(ticket: str | None) -> bool:
    """Atomically claim a login ticket: single-use (popped) and TTL-checked."""
    expiry = _login_tickets.pop(ticket, None) if ticket else None
    return expiry is not None and expiry >= time.time()


def require_api_token(
    x_gamegate_token: Annotated[str | None, Header()] = None,
    gamegate_token: Annotated[str | None, Cookie()] = None,
) -> None:
    """Programs send the master token in the header; the browser dashboard
    sends the signed session cookie set by /app?key=... — either authenticates."""
    expected = get_settings().api_token
    if expected is None:
        return
    if x_gamegate_token and constant_time_equals(x_gamegate_token, expected):
        return
    if verify_session_cookie(gamegate_token, expected):
        return
    raise HTTPException(status_code=401, detail="Missing or invalid API token")
