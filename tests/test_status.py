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


def test_recap_not_starved_by_a_large_out_of_window_backlog(client):
    """Review MAJOR (LIMIT-before-filter): a big backlog of never-consumed
    'away'/'focused' events (delivered=0 forever under decision C) must NOT push
    a game's real in-window message out of its recap. Filtering the window in
    SQL keeps the 1000-row cap on the rows that matter. Fails on the old code,
    which took undelivered()'s 1000 oldest rows and filtered in Python.
    """
    from datetime import UTC, datetime, timedelta

    from app.db import get_database
    from tests.test_events import make_event

    now = datetime.now(UTC)
    # 1001 stale, out-of-window events, oldest created_at — bulk insert for speed.
    conn = get_database().connection()
    with conn:
        for i in range(1001):
            ts = (now - timedelta(days=30) + timedelta(seconds=i)).isoformat()
            conn.execute(
                "INSERT INTO events (id, source, external_id, sender, title,"
                " content, received_at, priority, requires_action, metadata,"
                " created_at, delivered) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                (f"stale-{i}", "gmail", f"stale-{i}", "old@x", "OLD", "",
                 ts, "informational", 0, "{}", ts),
            )

    start = now - timedelta(minutes=10)
    client.post("/status", json={
        "state": "gaming", "application": "g.exe", "started_at": start.isoformat()})
    client.post("/events", json=make_event(
        external_id="in-window", title="REAL-INGAME", priority="informational",
        requires_action=False, received_at=(now - timedelta(minutes=2)).isoformat()))
    client.post("/status", json={"state": "available"})  # build the recap

    import json as _json
    recap = client.get("/digest/latest").json()
    assert "REAL-INGAME" in _json.dumps(recap["items"])  # not starved out
    assert recap["total_events"] == 1  # only the in-window message, backlog excluded


def test_switching_games_mid_session_makes_a_separate_recap(client):
    """Quit one game and start another without going available: each game gets
    its own session and digest, not one merged recap (M7)."""
    client.post("/status", json={"state": "gaming", "application": "helldivers2.exe"})
    client.post("/status", json={"state": "gaming", "application": "fortnite.exe"})  # switch
    client.post("/status", json={"state": "available"})
    digests = client.get("/digests").json()
    assert len(digests) == 2  # one recap per game, not a single merged one
