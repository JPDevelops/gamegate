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
    leftovers = list(tmp_path.glob(".env.*.tmp"))    # atomic swap cleaned up
    assert leftovers == []
    if os.name == "posix":  # POSIX file modes only — Windows has no 0600 (M7)
        assert oct(env.stat().st_mode)[-3:] == "600"  # secrets file is 0600


def test_update_env_var_survives_concurrent_writers(tmp_path, monkeypatch):
    """MAJOR #5: N connector toggles firing at once must all persist — no
    lost updates and no corruption. The old fixed-name unlocked read-modify-
    write could drop updates when two writers read the same old content."""
    import threading

    env = tmp_path / ".env"
    env.write_text("BASE=1\n")
    monkeypatch.setattr(connectors_module, "ENV_PATH", env)

    n = 24
    start = threading.Barrier(n)

    def writer(i):
        start.wait()  # release all threads into the write at once
        connectors_module.update_env_var(f"KEY_{i:02d}", "true")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    text = env.read_text()
    for i in range(n):
        assert f"KEY_{i:02d}=true" in text, f"lost update for KEY_{i:02d}"
    assert list(tmp_path.glob(".env.*.tmp")) == []  # no stray temp files


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
    # Discord + Slack aren't configurable connectors on the local app, so the
    # catalog only offers the ones you actually set up.
    assert ids == ["gmail", "text", "classifier", "artwork"]


def _post_text(client, ext="t1"):
    return client.post("/events", json={
        "source": "text", "external_id": ext, "sender": "Text", "title": "Mom",
        "content": "running late", "received_at": "2026-08-25T10:00:00+00:00",
        "priority": "informational",
    })


def test_text_sync_connector_four_state_model(client):
    # State 1 — never set up: a setup-walkthrough connector, not a toggle.
    t = client.get("/connections").json()["text"]
    assert t["state"] == "disconnected" and t["kind"] == "textsync"
    assert t["connect_label"] == "Sync text messages"

    # State 2 — enabled but no text captured yet: "waiting", resume at step 2.
    assert client.post("/settings/text-sync", json={"enabled": True}).json()["enabled"] is True
    t = client.get("/connections").json()["text"]
    assert t["state"] == "needs setup" and t["resume_step"] == 2
    assert t["can_disconnect"] is True

    # State 3 — a text actually arrived: connected.
    assert _post_text(client).status_code in (200, 201)
    t = client.get("/connections").json()["text"]
    assert t["state"] == "connected" and t["can_disconnect"] is True

    # State 4 — turned off but a text exists (phone still paired): resume at step 4.
    client.post("/settings/text-sync", json={"enabled": False})
    t = client.get("/connections").json()["text"]
    assert t["state"] == "needs setup" and t["resume_step"] == 4
    assert t["connect_label"] == "Turn syncing back on"


def test_phone_link_endpoints_are_safe_everywhere(client):
    # Status never 500s and always reports a tri-state (True/False/None off-Windows).
    s = client.get("/system/phone-link-status")
    assert s.status_code == 200 and s.json()["installed"] in (True, False, None)
    # Launch/install just report launched:bool and never raise (no-op off-Windows).
    for path in ("/system/open-phone-link", "/system/get-phone-link"):
        r = client.post(path)
        assert r.status_code == 200 and "launched" in r.json()


def test_text_probe_detects_a_text_after_since(client):
    before = "2026-01-01T00:00:00+00:00"
    assert client.get("/connectors/text/probe", params={"since": before}).json()["captured"] is False
    _post_text(client, "probe1")
    got = client.get("/connectors/text/probe", params={"since": before}).json()
    assert got["captured"] is True and got["sender"] == "Text"
    # The wizard takes its wait-baseline from this server clock, not the browser's.
    assert "now" in got and got["now"]
    # A baseline AFTER the text must not re-report it (no false positive).
    later = client.get("/connectors/text/probe", params={"since": got["now"]}).json()
    assert later["captured"] is False


def test_steamgriddb_key_verify_store_and_expose(client, monkeypatch):
    """The game-art (SteamGridDB) key connector: a bad key is rejected at save,
    a good key is stored + exposed only as a boolean + sets the art env, and the
    'artwork' connector flips to connected. Verify is mocked (no network)."""
    import app.api.art as art_api  # the endpoint imports verify from here

    # Reject a bad key (don't store).
    monkeypatch.setattr(art_api, "verify_steamgriddb_key",
                        lambda k, **kw: (False, "SteamGridDB rejected that key"))
    r = client.post("/settings/steamgriddb", json={"api_key": "bad"})
    assert r.status_code == 400
    assert client.get("/settings").json()["steamgriddb_api_key_set"] is False

    # Accept a good key.
    monkeypatch.setattr(art_api, "verify_steamgriddb_key", lambda k, **kw: (True, ""))
    r = client.post("/settings/steamgriddb", json={"api_key": "good-key"})
    assert r.status_code == 200 and r.json()["api_key_set"] is True
    s = client.get("/settings").json()
    assert s["steamgriddb_api_key_set"] is True
    assert "steamgriddb_api_key" not in s  # the secret itself is never returned

    # The artwork connector now reads connected.
    art = client.get("/connections").json()["artwork"]
    assert art["state"] == "connected"

    # Clearing it removes the key + flips back to disconnected.
    r = client.post("/settings/steamgriddb", json={"api_key": ""})
    assert r.status_code == 200 and r.json()["api_key_set"] is False
    assert client.get("/connections").json()["artwork"]["state"] == "disconnected"
