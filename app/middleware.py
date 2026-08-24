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
    "img-src 'self' https://cdn.cloudflare.steamstatic.com data:; "
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
        # Trust only the LAST X-Forwarded-For hop — the one our own nginx
        # appends. The client-supplied left entries are attacker-controlled
        # (spoofing them bypassed the limiter and could frame other IPs).
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[-1].strip()
        return request.client.host if request.client else "unknown"

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
        if response.status_code == 401:
            fails = self._failures[ip]
            fails.append(now)
            while fails and now - fails[0] > self.window_s:
                fails.popleft()
            if len(fails) >= self.max_failures:
                self._blocked[ip] = now + self.cooldown_s
                fails.clear()
            self._sweep(now)
        elif response.status_code < 400:
            self._failures.pop(ip, None)  # success clears the counter
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
