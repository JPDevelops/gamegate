from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import __version__
from app import db as db_module
from app.api.art import router as art_router
from app.api.classify import router as classify_router
from app.api.connectors import router as connectors_router
from app.api.dashboard import router as dashboard_router
from app.api.digest import router as digest_router
from app.api.events import router as events_router
from app.api.gmail_oauth import router as gmail_oauth_router
from app.api.settings import router as settings_router
from app.api.status import router as status_router
from app.config import get_settings
from app.middleware import AuthRateLimitMiddleware, SecurityHeadersMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Fail closed: refuse to start unauthenticated unless the operator has
    # EXPLICITLY opted into open dev mode with GAMEGATE_ENV=development. An
    # unset token with an unset/other env is the accidental-open case the
    # docs' `cp .env.example .env` path used to produce silently — now it
    # stops the server instead of serving every endpoint without auth.
    if not settings.api_token and not settings.explicit_dev:
        raise RuntimeError(
            "GAMEGATE_API_TOKEN is required. To run without auth locally, set "
            "GAMEGATE_ENV=development explicitly."
        )
    if db_module._database is None:
        db_module.init_database(settings.db_path)
    from app.middleware import reset_rate_limits
    reset_rate_limits()
    yield


# Interactive docs (/docs, /redoc, /openapi.json) are open by default and would
# hand an unauthenticated caller the full endpoint inventory. Enable them only
# in development; disable in production so "only /health is open" is actually
# true (M4).
_docs_enabled = get_settings().env != "production"
app = FastAPI(
    title="GameGate",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)
# Order matters: Starlette makes the LAST-added middleware outermost, so add
# the rate limiter FIRST and the header middleware LAST. That way the header
# middleware wraps everything — including the rate limiter's early 429 — so
# even throttled responses carry CSP/nosniff/Cache-Control (M11).
app.add_middleware(AuthRateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


@app.exception_handler(RecursionError)
async def _too_deep(request: Request, exc: RecursionError) -> JSONResponse:
    # Deeply-nested JSON blew the parser's recursion limit — client error,
    # not a server fault. Return a clean 400 instead of a 500.
    return JSONResponse(status_code=400, content={"detail": "Payload too deeply nested"})
app.include_router(status_router)
app.include_router(events_router)
app.include_router(digest_router)
app.include_router(classify_router)
app.include_router(gmail_oauth_router)
app.include_router(dashboard_router)
app.include_router(art_router)
app.include_router(connectors_router)
app.include_router(settings_router)


class HealthResponse(BaseModel):
    status: str
    version: str


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
