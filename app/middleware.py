"""Security middleware (pre-launch checklist items 9, 11, 18, 19).

- SecurityHeadersMiddleware: standard hardening headers on every response,
  plus HSTS when the request arrived over HTTPS (nginx sets X-Forwarded-Proto).
- AuthRateLimitMiddleware: throttles repeated auth failures per client IP,
  the login-rate-limit equivalent for a token-authenticated API.
"""
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

CSP = (
    "default-src 'self'; "
    # /art 307-redirects to the SteamGridDB CDN; browsers apply img-src to the
    # redirect target, so the CDN host must be allowed or the artwork is blocked
    # by our own policy (M9).
    "img-src 'self' https://cdn.cloudflare.steamstatic.com "
    "https://cdn2.steamgriddb.com https://cdn.steamgriddb.com data:; "
    "style-src 'self' 'unsafe-inline'; "        # dashboard uses inline styles
    "script-src 'self' 'unsafe-inline'; "        # and inline handlers
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def _is_https(request: Request) -> bool:
    if request.headers.get("x-forwarded-proto", "").lower() == "https":
        return True
    return request.url.scheme == "https"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-XSS-Protection", "0")  # modern guidance: rely on CSP
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if _is_https(request):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        # Authenticated responses may carry personal data (messages, digests) or
        # the token itself (in the /app?key= URL). Forbid shared/proxy/disk
        # caching for any request that presented credentials; the public
        # marketing site and static assets stay cacheable.
        if _is_authenticated(request):
            response.headers["Cache-Control"] = "no-store"
        return response


def _is_authenticated(request: Request) -> bool:
    return bool(
        request.headers.get("x-gamegate-token")
        or request.cookies.get("gamegate_token")
        or request.query_params.get("key")
    )


# Peers whose X-Forwarded-For we trust — only our own loopback reverse proxy.
_TRUSTED_PROXIES = {"127.0.0.1", "::1"}

_failures: dict[str, deque] = defaultdict(deque)
_blocked: dict[str, float] = {}


def reset_rate_limits() -> None:
    _failures.clear()
    _blocked.clear()


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """After N auth failures (401) from one IP within the window, block that
    IP for a cooldown. Cheap in-memory defense against token brute-forcing."""

    def __init__(self, app, max_failures: int = 8, window_s: int = 60, cooldown_s: int = 300):
        super().__init__(app)
        self.max_failures = max_failures
        self.window_s = window_s
        self.cooldown_s = cooldown_s
        self._failures = _failures
        self._blocked = _blocked

    def _now(self) -> float:
        return time.monotonic()

    def _client(self, request: Request) -> str:
        # Only trust X-Forwarded-For when the request actually came from our
        # local reverse proxy (peer == 127.0.0.1/::1). Otherwise a client
        # reaching the app directly could send a single-entry XFF to mint a
        # fresh identity per request (bypassing the limiter) or spoof a victim's
        # IP to lock them out (M3). Direct callers are keyed by their real peer.
        peer = request.client.host if request.client else "unknown"
        if peer in _TRUSTED_PROXIES:
            fwd = request.headers.get("x-forwarded-for", "")
            if fwd:
                # nginx REPLACES XFF with $remote_addr (one hop); last entry is
                # the real client even if some upstream appended.
                return fwd.split(",")[-1].strip()
        return peer

    async def dispatch(self, request: Request, call_next):
        ip = self._client(request)
        now = self._now()
        until = self._blocked.get(ip)
        if until and now < until:
            return JSONResponse(
                {"detail": "Too many failed attempts. Try again shortly."},
                status_code=429,
            )
        response: Response = await call_next(request)
        # A keyless GET /app returns the 401 login page, but that's just viewing
        # the login screen, not a credential guess — counting it would let the
        # owner lock their own IP by refreshing a bookmarked /app (review MINOR).
        # Only an actual ?key= attempt on /app is a guess worth throttling.
        is_login_page_view = (
            request.url.path == "/app" and "key" not in request.query_params
        )
        if response.status_code == 401 and not is_login_page_view:
            fails = self._failures[ip]
            fails.append(now)
            while fails and now - fails[0] > self.window_s:
                fails.popleft()
            if len(fails) >= self.max_failures:
                self._blocked[ip] = now + self.cooldown_s
                fails.clear()
            self._sweep(now)
        # NOTE: failures are NOT cleared on a successful response. They expire on
        # their own after window_s. Clearing on any 2xx let an attacker reset the
        # counter with an unauthenticated GET /health between guesses, defeating
        # the throttle entirely (review M1).
        return response

    def _sweep(self, now: float) -> None:
        """Bound memory: drop IPs whose failure window has fully elapsed and
        blocks that have expired. Without this, any IP that fails once and never
        succeeds would leave a deque in memory forever (M12). Cheap — only runs
        on a 401 and only walks the maps when they grow past a threshold."""
        if len(self._failures) > 1024:
            for key in [
                k for k, d in self._failures.items()
                if not d or now - d[-1] > self.window_s
            ]:
                self._failures.pop(key, None)
        if len(self._blocked) > 1024:
            for key in [k for k, until in self._blocked.items() if until < now]:
                self._blocked.pop(key, None)
