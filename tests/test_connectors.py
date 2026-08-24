"""Connector lifecycle endpoints — connect/disconnect just flip an .env flag;
no sudo/systemd involved (review B2)."""
import os

import pytest

from app.api import connectors as connectors_module


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
    if os.name == "posix":  # POSIX file modes only — Windows has no 0600 (M7)
        assert oct(env.stat().st_mode)[-3:] == "600"  # secrets file is 0600


def test_gmail_disconnect_removes_token_and_flag(client, env_file, tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setenv("GMAIL_TOKEN_PATH", str(token))

    response = client.post("/connectors/gmail/disconnect")
    assert response.status_code == 200
    assert not token.exists()
    assert "GMAIL_ENABLED=false" in env_file.read_text()
    assert connectors_module.connector_enabled("gmail") is False  # poller stops on the flag


def test_gmail_connect_without_token_points_to_oauth(client, env_file, tmp_path, monkeypatch):
    monkeypatch.setenv("GMAIL_TOKEN_PATH", str(tmp_path / "missing.json"))
    body = client.post("/connectors/gmail/connect").json()
    assert body == {"authorize": "/connect/gmail"}


def test_gmail_connect_with_token_enables_flag(client, env_file, tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setenv("GMAIL_TOKEN_PATH", str(token))
    assert client.post("/connectors/gmail/connect").status_code == 200
    assert "GMAIL_ENABLED=true" in env_file.read_text()
    assert connectors_module.connector_enabled("gmail") is True


def test_discord_connect_disconnect_flips_flag_no_sudo(client, env_file):
    """B2: connect/disconnect only flip the .env flag — no systemctl/sudo — and
    service_active reads that flag."""
    client.post("/connectors/discord/connect")
    assert connectors_module.connector_enabled("discord") is True
    assert connectors_module.service_active("discord") is True
    client.post("/connectors/discord/disconnect")
    assert connectors_module.connector_enabled("discord") is False
    assert connectors_module.service_active("discord") is False


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
