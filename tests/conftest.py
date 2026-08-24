import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import get_settings
from app.main import app
from app.middleware import reset_rate_limits


@pytest.fixture(autouse=True)
def _isolate():
    """Every test starts with clean settings cache + rate-limit state, so a
    token set by one test (or accumulated 401s) can't leak into the next."""
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
