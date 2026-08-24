import os

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import get_settings
from app.main import app
from app.middleware import reset_rate_limits

# Env prefixes that configure GameGate. Scrubbed before every test so the suite
# is hermetic — a real `.env` sourced into the shell can no longer make tests
# pass or fail for the wrong reason (M18).
_MANAGED_ENV_PREFIXES = (
    "GAMEGATE_", "GMAIL_", "SLACK_", "DISCORD_", "OPENAI_", "CLASSIFIER_",
    "STEAMGRIDDB_",
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Every test starts from a clean, explicit environment: managed env vars
    scrubbed, settings cache + rate-limit state reset. GAMEGATE_ENV is set to
    'development' so the no-token case runs open by explicit intent rather than
    tripping the fail-closed startup guard."""
    for key in list(os.environ):
        if key.startswith(_MANAGED_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GAMEGATE_ENV", "development")
    get_settings.cache_clear()
    reset_rate_limits()
    yield
    get_settings.cache_clear()
    reset_rate_limits()


@pytest.fixture()
def client(tmp_path):
    """TestClient backed by a fresh temporary database per test."""
    db_module.init_database(str(tmp_path / "test.db"))
    with TestClient(app) as test_client:
        yield test_client
