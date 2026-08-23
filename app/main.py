from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from app import __version__
from app import db as db_module
from app.api.events import router as events_router
from app.api.status import router as status_router
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    if db_module._database is None:
        db_module.init_database(get_settings().db_path)
    yield


app = FastAPI(title="GameGate", version=__version__, lifespan=lifespan)
app.include_router(status_router)
app.include_router(events_router)


class HealthResponse(BaseModel):
    status: str
    version: str


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
