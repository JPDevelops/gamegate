from fastapi import FastAPI
from pydantic import BaseModel

from app import __version__
from app.api.events import router as events_router
from app.api.status import router as status_router

app = FastAPI(title="GameGate", version=__version__)
app.include_router(status_router)
app.include_router(events_router)


class HealthResponse(BaseModel):
    status: str
    version: str


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
