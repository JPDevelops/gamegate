"""Dashboard: cookie login exchange, per-connector truth, digest history."""
import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import get_settings
from app.main import app


@pytest.fixture()
def secured(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEGATE_API_TOKEN", "dash-secret")
    get_settings.cache_clear()
    db_module.init_database(str(tmp_path / "t.db"))
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_app_without_key_or_cookie_shows_login(secured):
    response = secured.get("/app")
    assert response.status_code == 401
    assert "access link" in response.text


def test_key_exchanges_for_cookie_then_serves_dashboard(secured):
    response = secured.get("/app", params={"key": "dash-secret"}, follow_redirects=False)
    assert response.status_code == 303
    set_cookie = response.headers["set-cookie"]
    assert "gamegate_token=" in set_cookie and "HttpOnly" in set_cookie
    # The cookie is a signed session token, NOT the raw master token (M9).
    assert "dash-secret" not in set_cookie
    # cookie persists on the client; the follow-up serves the page
    page = secured.get("/app")
    assert page.status_code == 200
    assert "GAMEGATE" in page.text


def test_logout_clears_cookie_and_relocks(secured):
    secured.get("/app", params={"key": "dash-secret"}, follow_redirects=False)
    assert secured.get("/events").status_code == 200  # logged in via cookie
    secured.post("/logout", follow_redirects=False)
    assert secured.get("/app").status_code == 401  # cookie cleared → locked again


def test_cookie_authenticates_data_endpoints(secured):
    secured.get("/app", params={"key": "dash-secret"}, follow_redirects=False)
    assert secured.get("/events").status_code == 200          # cookie, no header
    assert secured.get("/connections").status_code == 200
    assert secured.get("/digests").status_code == 200


def test_connections_reports_truth(client, monkeypatch):
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("SLACK_ENABLED", raising=False)
    monkeypatch.delenv("CLASSIFIER_ENABLED", raising=False)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    body = client.get("/connections").json()
    assert body["gmail"]["state"] == "disabled"
    assert body["slack"]["state"] == "disabled"
    assert body["discord"]["state"] == "needs setup"
    assert "Version" in body["settings"]


def test_digest_history_lists_recent_with_rendered_text(client):
    client.post("/status", json={"state": "gaming", "application": "g.exe"})
    client.post("/status", json={"state": "available"})
    history = client.get("/digests").json()
    assert len(history) == 1
    assert history[0]["session_id"]
    # Jules' live find: cards rendered empty because text was never included.
    assert "Game Recap" in history[0]["text"]
