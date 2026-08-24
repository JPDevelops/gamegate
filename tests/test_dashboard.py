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


def test_one_time_ticket_logs_in_without_the_token_in_the_url(secured):
    """MAJOR: a holder of the master token mints a single-use ticket over the
    authenticated header, then /app?ticket= logs the browser in and swaps for a
    cookie — the master token never appears in a URL. The ticket is one-use."""
    minted = secured.post("/auth/ticket", headers={"X-GameGate-Token": "dash-secret"})
    assert minted.status_code == 200
    ticket = minted.json()["ticket"]

    resp = secured.get("/app", params={"ticket": ticket}, follow_redirects=False)
    assert resp.status_code == 303
    assert "gamegate_token=" in resp.headers["set-cookie"]
    assert secured.get("/app").status_code == 200          # cookie now authenticates

    # Single-use: the same ticket can't log a fresh browser in again.
    fresh = TestClient(app)
    assert fresh.get("/app", params={"ticket": ticket}).status_code == 401


def test_minting_a_ticket_requires_auth(secured):
    assert secured.post("/auth/ticket").status_code == 401


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
    # The sidebar version is injected from the package version, not hardcoded,
    # so it can't drift from pyproject again (old bug: static "v0.1" on 0.2.0).
    from app import __version__
    assert f"v{__version__}" in page.text
    assert "%%GAMEGATE_VERSION%%" not in page.text  # placeholder was filled


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


def test_connector_heartbeat_drives_health(client, monkeypatch, tmp_path):
    """MAJOR: 'connected' should reflect live health, not just the enable flag.
    A connected Gmail whose last heartbeat was an error shows as 'degraded'."""
    token = tmp_path / "token.json"
    token.write_text("{}")  # exists → gmail counts as 'connected'
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GMAIL_ENABLED", "true")
    monkeypatch.setenv("GMAIL_TOKEN_PATH", str(token))

    # healthy heartbeat → stays connected
    client.post("/connectors/gmail/heartbeat", json={"ok": True})
    assert client.get("/connections").json()["gmail"]["state"] == "connected"
    # a later error heartbeat → degraded, with the detail surfaced
    client.post("/connectors/gmail/heartbeat", json={"ok": False, "detail": "token expired"})
    gmail = client.get("/connections").json()["gmail"]
    assert gmail["state"] == "degraded"
    assert "token expired" in gmail["detail"]


def test_silent_connector_goes_stale(client, monkeypatch, tmp_path):
    """MINOR: a connector that crashes/OOMs just STOPS beating (no error). An old
    last_success must show 'degraded/stale', not a permanent green connected."""
    from datetime import UTC, datetime, timedelta

    from app.db import get_database

    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GMAIL_ENABLED", "true")
    monkeypatch.setenv("GMAIL_TOKEN_PATH", str(token))

    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()  # last beat an hour ago
    with get_database().connection() as conn:
        conn.execute(
            "INSERT INTO connector_health (name, last_success, updated_at)"
            " VALUES ('gmail', ?, ?)", (old, old))
    gmail = client.get("/connections").json()["gmail"]
    assert gmail["state"] == "degraded"
    assert "No heartbeat" in gmail["detail"]


def test_agent_update_status_surfaces_in_connections(client):
    """The tray reports pending updates; the dashboard Settings area reads the
    same state so it can show 'Latest version' vs 'Update available' (review)."""
    from app.services import agent_status

    try:
        client.post("/agent/update-status", json={"pending": 0, "build": "abc123"})
        agent = client.get("/connections").json()["agent"]
        assert agent["pending"] == 0 and agent["build"] == "abc123"

        client.post("/agent/update-status", json={"pending": 3, "build": "abc123"})
        assert client.get("/connections").json()["agent"]["pending"] == 3
    finally:
        agent_status._state.update({"pending": None, "build": None, "at": None})


def test_digest_history_lists_recent_with_rendered_text(client):
    client.post("/status", json={"state": "gaming", "application": "g.exe"})
    client.post("/status", json={"state": "available"})
    history = client.get("/digests").json()
    assert len(history) == 1
    assert history[0]["session_id"]
    # Jules' live find: cards rendered empty because text was never included.
    assert "Game Recap" in history[0]["text"]
