"""Step 14: write endpoints reject unauthenticated requests when a token
is configured; reads stay open; no token configured = dev mode, auth off."""
import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import get_settings
from app.main import app
from tests.test_events import make_event


@pytest.fixture()
def secured_client(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEGATE_API_TOKEN", "test-secret")
    get_settings.cache_clear()
    db_module.init_database(str(tmp_path / "test.db"))
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_write_without_token_is_401(secured_client):
    assert secured_client.post("/status", json={"state": "focused"}).status_code == 401
    assert secured_client.post("/events", json=make_event()).status_code == 401


def test_write_with_wrong_token_is_401(secured_client):
    response = secured_client.post(
        "/status", json={"state": "focused"}, headers={"X-GameGate-Token": "wrong"}
    )
    assert response.status_code == 401


def test_write_with_token_succeeds_and_reads_stay_open(secured_client):
    headers = {"X-GameGate-Token": "test-secret"}
    assert (
        secured_client.post("/status", json={"state": "focused"}, headers=headers).status_code
        == 200
    )
    assert secured_client.get("/status").status_code == 200
    assert secured_client.get("/health").status_code == 200


def test_no_token_configured_means_auth_disabled(client):
    # The plain `client` fixture has no GAMEGATE_API_TOKEN set.
    assert client.post("/status", json={"state": "focused"}).status_code == 200
