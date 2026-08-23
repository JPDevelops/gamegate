"""Step 14: with a token configured, ALL data endpoints require it (reads
included — digests and events carry private message content); /health stays
open. No token + development env = auth off. Production without a token
refuses to start."""
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


def test_reads_with_data_require_token_too(secured_client):
    assert secured_client.get("/events").status_code == 401
    assert secured_client.get("/digest").status_code == 401
    assert secured_client.get("/notifications/pending").status_code == 401
    assert secured_client.get("/status").status_code == 401


def test_health_stays_open(secured_client):
    assert secured_client.get("/health").status_code == 200


def test_correct_token_works_everywhere(secured_client):
    headers = {"X-GameGate-Token": "test-secret"}
    assert (
        secured_client.post("/status", json={"state": "focused"}, headers=headers).status_code
        == 200
    )
    assert secured_client.get("/events", headers=headers).status_code == 200
    assert secured_client.get("/digest", headers=headers).status_code == 200


def test_wrong_token_is_401(secured_client):
    response = secured_client.post(
        "/status", json={"state": "focused"}, headers={"X-GameGate-Token": "wrong"}
    )
    assert response.status_code == 401


def test_no_token_configured_means_auth_disabled(client):
    assert client.post("/status", json={"state": "focused"}).status_code == 200


def test_production_without_token_refuses_to_start(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEGATE_ENV", "production")
    monkeypatch.delenv("GAMEGATE_API_TOKEN", raising=False)
    get_settings.cache_clear()
    db_module.init_database(str(tmp_path / "test.db"))
    with pytest.raises(RuntimeError, match="GAMEGATE_API_TOKEN is required"), TestClient(app):
        pass
    get_settings.cache_clear()


def test_events_limit_is_capped(client):
    assert client.get("/events", params={"limit": 5000}).status_code == 422
    assert client.get("/events", params={"limit": 0}).status_code == 422
