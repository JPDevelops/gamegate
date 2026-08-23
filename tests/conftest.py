import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app


@pytest.fixture()
def client(tmp_path):
    """TestClient backed by a fresh temporary database per test."""
    db_module.init_database(str(tmp_path / "test.db"))
    with TestClient(app) as test_client:
        yield test_client
