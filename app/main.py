from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from app import __version__
from app import db as db_module
from app.api.classify import router as classify_router
from app.api.digest import router as digest_router
from app.api.events import router as events_router
from app.api.status import router as status_router
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Fail closed (Nebula audit #3): production must never run unauthenticated.
    if settings.env == "production" and not settings.api_token:
        raise RuntimeError(
            "GAMEGATE_API_TOKEN is required when GAMEGATE_ENV=production"
        )
    if db_module._database is None:
        db_module.init_database(settings.db_path)
    yield


app = FastAPI(title="GameGate", version=__version__, lifespan=lifespan)
app.include_router(status_router)
app.include_router(events_router)
app.include_router(digest_router)
app.include_router(classify_router)


class HealthResponse(BaseModel):
    status: str
    version: str


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
