"""Connector lifecycle endpoints — env/file effects with systemd stubbed."""
import os

import pytest

from app.api import connectors as connectors_module


@pytest.fixture(autouse=True)
def no_systemd(monkeypatch):
    calls = []
    monkeypatch.setattr(connectors_module, "_systemctl", lambda a, u: calls.append((a, u)))
    yield calls


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GMAIL_ENABLED=true\n")
    monkeypatch.setattr(connectors_module, "ENV_PATH", env)
    return env


def test_update_env_var_does_not_uncomment_or_leave_tmp(tmp_path, monkeypatch):
    """M12: a commented-out key must NOT be uncommented, an active key IS
    replaced, and the atomic write leaves no .env.tmp behind."""
    env = tmp_path / ".env"
    env.write_text("# GMAIL_ENABLED=false\nCLASSIFIER_ENABLED=false\n")
    monkeypatch.setattr(connectors_module, "ENV_PATH", env)

    connectors_module.update_env_var("GMAIL_ENABLED", "true")   # only commented → append
    connectors_module.update_env_var("CLASSIFIER_ENABLED", "true")  # active → replace in place
    text = env.read_text()

    assert "# GMAIL_ENABLED=false" in text          # comment left intact
    assert "GMAIL_ENABLED=true" in text             # active value appended
    assert "CLASSIFIER_ENABLED=true" in text
    assert "CLASSIFIER_ENABLED=false" not in text   # replaced, not duplicated
    assert not (tmp_path / ".env.tmp").exists()     # atomic swap cleaned up


def test_gmail_disconnect_removes_token_and_flag(client, env_file, tmp_path, monkeypatch, no_systemd):
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setenv("GMAIL_TOKEN_PATH", str(token))

    response = client.post("/connectors/gmail/disconnect")
    assert response.status_code == 200
    assert not token.exists()
    assert "GMAIL_ENABLED=false" in env_file.read_text()
    assert ("stop", "gamegate-gmail") in no_systemd


def test_gmail_connect_without_token_points_to_oauth(client, env_file, tmp_path, monkeypatch):
    monkeypatch.setenv("GMAIL_TOKEN_PATH", str(tmp_path / "missing.json"))
    body = client.post("/connectors/gmail/connect").json()
    assert body == {"authorize": "/connect/gmail"}


def test_gmail_connect_with_token_enables_and_starts(client, env_file, tmp_path, monkeypatch, no_systemd):
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setenv("GMAIL_TOKEN_PATH", str(token))
    assert client.post("/connectors/gmail/connect").status_code == 200
    assert "GMAIL_ENABLED=true" in env_file.read_text()
    assert ("start", "gamegate-gmail") in no_systemd


def test_classifier_toggle_persists_flag(client, env_file):
    client.post("/connectors/classifier/connect")
    assert "CLASSIFIER_ENABLED=true" in env_file.read_text()
    assert os.environ["CLASSIFIER_ENABLED"] == "true"
    client.post("/connectors/classifier/disconnect")
    assert "CLASSIFIER_ENABLED=false" in env_file.read_text()


def test_slack_connect_explains_deferral(client):
    response = client.post("/connectors/slack/connect")
    assert response.status_code == 409
    assert "later version" in response.json()["detail"]


def test_unknown_connector_is_404(client):
    assert client.post("/connectors/winamp/connect").status_code == 404


def test_catalog_present_in_connections(client):
    body = client.get("/connections").json()
    ids = [c["id"] for c in body["catalog"]]
    assert ids == ["discord", "gmail", "slack", "classifier"]
