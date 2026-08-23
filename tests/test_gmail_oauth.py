"""Connect-Gmail flow: config gating, state handling, token storage.
No live Google calls — exchange goes through httpx.MockTransport."""
import json

import httpx
import pytest

from app.api import gmail_oauth


@pytest.fixture()
def oauth_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "cid-123")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "csec-456")
    monkeypatch.setenv("GMAIL_TOKEN_PATH", str(tmp_path / "token.json"))
    gmail_oauth._pending_states.clear()
    yield tmp_path
    gmail_oauth._pending_states.clear()


def test_connect_without_client_config_is_503(client, monkeypatch):
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_SECRET", raising=False)
    response = client.get("/connect/gmail", follow_redirects=False)
    assert response.status_code == 503


def test_connect_redirects_to_google_with_state(client, oauth_env):
    response = client.get("/connect/gmail", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "gmail.readonly" in location
    assert "access_type=offline" in location
    assert len(gmail_oauth._pending_states) == 1


def test_callback_rejects_unknown_state(client, oauth_env):
    response = client.get("/oauth/gmail/callback", params={"code": "x", "state": "bogus"})
    assert response.status_code == 400


def test_exchange_code_parses_google_response():
    def handler(request):
        assert request.url == httpx.URL(gmail_oauth.TOKEN_URL)
        return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1"})

    tokens = gmail_oauth.exchange_code(
        "authcode", "cid", "csec", "https://x/cb",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert tokens["refresh_token"] == "rt-1"


def test_exchange_code_provider_error_is_502():
    from fastapi import HTTPException

    def handler(request):
        return httpx.Response(500, text="boom")

    with pytest.raises(HTTPException) as excinfo:
        gmail_oauth.exchange_code(
            "authcode", "cid", "csec", "https://x/cb",
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )
    assert excinfo.value.status_code == 502


def test_callback_exchanges_and_stores_token(client, oauth_env, monkeypatch):
    monkeypatch.setattr(
        gmail_oauth, "exchange_code",
        lambda code, cid, cs, ru, http=None: {"access_token": "at-1", "refresh_token": "rt-1"},
    )
    # issue a real state via the connect endpoint
    client.get("/connect/gmail", follow_redirects=False)
    state = next(iter(gmail_oauth._pending_states))

    response = client.get("/oauth/gmail/callback", params={"code": "authcode", "state": state})
    assert response.status_code == 200
    assert "Gmail connected" in response.text

    token = json.loads((oauth_env / "token.json").read_text())
    assert token["refresh_token"] == "rt-1"
    assert token["client_id"] == "cid-123"
    assert token["scopes"] == [gmail_oauth.SCOPE]
    # state is single-use
    assert client.get(
        "/oauth/gmail/callback", params={"code": "authcode", "state": state}
    ).status_code == 400


def test_connect_requires_key_when_api_token_set(oauth_env, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import db as db_module
    from app.config import get_settings
    from app.main import app

    monkeypatch.setenv("GAMEGATE_API_TOKEN", "sekret")
    get_settings.cache_clear()
    db_module.init_database(str(tmp_path / "t.db"))
    with TestClient(app) as secured:
        assert secured.get("/connect/gmail", follow_redirects=False).status_code == 401
        ok = secured.get(
            "/connect/gmail", params={"key": "sekret"}, follow_redirects=False
        )
        assert ok.status_code == 307
    get_settings.cache_clear()
