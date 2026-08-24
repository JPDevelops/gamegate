def test_initial_state_is_available(client):
    response = client.get("/status")
    assert response.status_code == 200
    assert response.json()["state"] == "available"


def test_post_gaming_status_and_read_back(client):
    payload = {
        "state": "gaming",
        "application": "helldivers2.exe",
        "started_at": "2026-08-22T20:31:00-07:00",
    }
    post = client.post("/status", json=payload)
    assert post.status_code == 200
    assert post.json()["state"] == "gaming"
    assert post.json()["application"] == "helldivers2.exe"
    assert client.get("/status").json()["state"] == "gaming"


def test_invalid_state_is_rejected(client):
    response = client.post("/status", json={"state": "napping"})
    assert response.status_code == 422
    assert client.get("/status").json()["state"] == "available"


def test_state_only_update_clears_application(client):
    client.post("/status", json={"state": "gaming", "application": "helldivers2.exe"})
    client.post("/status", json={"state": "available"})
    body = client.get("/status").json()
    assert body["state"] == "available"
    assert body["application"] is None


def test_application_control_chars_stripped(client):
    """M4: newlines/control chars in the application name are stripped so they
    can't forge lines in journald when logged; length is capped."""
    client.post("/status", json={
        "state": "gaming",
        "application": "Rust\n2026-01-01 FAKE LOG LINE\x00" + "x" * 300,
    })
    app = client.get("/status").json()["application"]
    assert "\n" not in app and "\x00" not in app
    assert len(app) <= 128


def test_recap_only_includes_messages_received_during_the_game(client):
    """B1 (owner decision C): the game recap contains ONLY messages that arrived
    during that game. A message received before you started playing stays in the
    Messages tab, never folded into the recap."""
    import json
    from datetime import UTC, datetime, timedelta

    from tests.test_events import make_event

    now = datetime.now(UTC)
    start = now - timedelta(minutes=10)
    # received BEFORE the session (a stale email while away):
    client.post("/events", json=make_event(
        external_id="before-1", title="STALE-BEFORE", priority="informational",
        requires_action=False, received_at=(now - timedelta(hours=2)).isoformat()))
    client.post("/status", json={
        "state": "gaming", "application": "g.exe", "started_at": start.isoformat()})
    # received DURING the session:
    client.post("/events", json=make_event(
        external_id="during-1", title="AROSE-DURING", priority="informational",
        requires_action=False, received_at=(now - timedelta(minutes=3)).isoformat()))
    client.post("/status", json={"state": "available"})  # build the recap

    recap = client.get("/digest/latest").json()
    assert recap["total_events"] == 1                 # only the in-window message
    blob = json.dumps(recap["items"])
    assert "AROSE-DURING" in blob and "STALE-BEFORE" not in blob


def test_switching_games_mid_session_makes_a_separate_recap(client):
    """Quit one game and start another without going available: each game gets
    its own session and digest, not one merged recap (M7)."""
    client.post("/status", json={"state": "gaming", "application": "helldivers2.exe"})
    client.post("/status", json={"state": "gaming", "application": "fortnite.exe"})  # switch
    client.post("/status", json={"state": "available"})
    digests = client.get("/digests").json()
    assert len(digests) == 2  # one recap per game, not a single merged one
