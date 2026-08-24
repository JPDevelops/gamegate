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


def test_started_at_far_in_the_future_is_clamped(client):
    """MAJOR #8: a bad detector clock can't open a session that starts in the
    far future (which would swallow events with an absurd window). It's clamped
    to ~now, and a naive timestamp is coerced to aware UTC."""
    from datetime import UTC, datetime, timedelta

    future = (datetime.now(UTC) + timedelta(days=3650)).replace(tzinfo=None)  # naive + far
    client.post("/status", json={
        "state": "gaming", "application": "g.exe", "started_at": future.isoformat()})
    got = client.get("/status").json()["started_at"]
    parsed = datetime.fromisoformat(got)
    assert parsed.tzinfo is not None                              # coerced to aware
    assert parsed <= datetime.now(UTC) + timedelta(minutes=6)     # clamped to ~now


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


def test_recap_window_handles_non_utc_offsets(client):
    """The recap window compares received_at as ISO strings in SQLite, so every
    stored timestamp must be normalized to a UTC offset. An event that arrives
    DURING the game but is expressed in a -07:00 offset has a raw string whose
    wall-clock hour sorts hours before the window start; only converting it to
    UTC (astimezone) puts it back in range. Fails on the old code, which kept the
    original offset and dropped this message from the recap."""
    from datetime import UTC, datetime, timedelta, timezone

    from tests.test_events import make_event

    now = datetime.now(UTC)
    start = now - timedelta(minutes=10)
    during_instant = now - timedelta(minutes=5)              # inside [start, now]
    during_pt = during_instant.astimezone(timezone(timedelta(hours=-7)))  # same instant, -07:00

    client.post("/status", json={
        "state": "gaming", "application": "g.exe", "started_at": start.isoformat()})
    client.post("/events", json=make_event(
        external_id="pt-1", title="PT-DURING", priority="informational",
        requires_action=False, received_at=during_pt.isoformat()))
    client.post("/status", json={"state": "available"})       # build the recap

    import json as _json
    recap = client.get("/digest/latest").json()
    assert "PT-DURING" in _json.dumps(recap["items"])          # not dropped by offset
    assert recap["total_events"] == 1


def test_switching_games_mid_session_makes_a_separate_recap(client):
    """Quit one game and start another without going available: each game gets
    its own session and digest, not one merged recap (M7)."""
    client.post("/status", json={"state": "gaming", "application": "helldivers2.exe"})
    client.post("/status", json={"state": "gaming", "application": "fortnite.exe"})  # switch
    client.post("/status", json={"state": "available"})
    digests = client.get("/digests").json()
    assert len(digests) == 2  # one recap per game, not a single merged one


def _digests(client):
    return client.get("/digests").json()


def test_dnd_override_wins_over_detector(client):
    """Owner decision: dashboard DND is a manual override the detector cannot
    overwrite. Turning it on holds you focused even if the detector reports
    gaming, and no session/recap is cut while it's on."""
    client.post("/status/dnd", json={"enabled": True})
    s = client.get("/status").json()
    assert s["state"] == "focused" and s["manual_override"] is True

    # Detector reports gaming while DND is on — held, not applied.
    client.post("/status", json={"state": "gaming", "application": "g.exe"})
    s = client.get("/status").json()
    assert s["state"] == "focused" and s["manual_override"] is True
    assert _digests(client) == []          # no recap cut while DND holds

    # Turn DND off — the detector's base state (gaming) surfaces again.
    client.post("/status/dnd", json={"enabled": False})
    s = client.get("/status").json()
    assert s["manual_override"] is False
    assert s["state"] == "gaming"          # the held detector state resumes


def test_enabling_dnd_while_gaming_closes_the_session(client):
    """Turning DND on mid-game closes the open session (you get the recap for
    what you played), then nothing new is cut while focused."""
    client.post("/status", json={"state": "gaming", "application": "g.exe"})
    assert len(_digests(client)) == 0
    client.post("/status/dnd", json={"enabled": True})
    assert len(_digests(client)) == 1      # the game session was recapped on DND-on
    # a further detector gaming poll under DND opens nothing new
    client.post("/status", json={"state": "gaming", "application": "g.exe"})
    assert len(_digests(client)) == 1


def test_turning_dnd_off_mid_game_reopens_the_session_without_a_detector_repost(client):
    """MAJOR regression: the real detector only POSTs on a transition, so if the
    same game keeps running across a DND window it does NOT re-announce 'gaming'.
    Turning DND off must therefore re-open the session ITSELF, so the post-DND
    stretch still gets recapped. The old code left base state 'gaming' with no
    session and silently lost that recap (this test does NOT re-POST gaming)."""
    # gaming, then DND on mid-game (recaps what was played), then DND off — with
    # NO further /status from the detector (the game never stopped).
    client.post("/status", json={"state": "gaming", "application": "g.exe"})
    client.post("/status/dnd", json={"enabled": True})
    assert len(_digests(client)) == 1            # recap of the pre-DND play
    client.post("/status/dnd", json={"enabled": False})
    # The session must be open again now, WITHOUT the detector re-posting.
    assert client.get("/status").json()["state"] == "gaming"
    # A message arriving during the post-DND stretch is captured by that session…
    from datetime import UTC, datetime

    from tests.test_events import make_event
    client.post("/events", json=make_event(
        external_id="post-dnd", title="AFTER-DND", priority="informational",
        requires_action=False, received_at=datetime.now(UTC).isoformat()))
    # …and folded into a SECOND recap when the game finally ends.
    client.post("/status", json={"state": "available"})
    import json as _json
    assert len(_digests(client)) == 2
    latest = client.get("/digest/latest").json()
    assert "AFTER-DND" in _json.dumps(latest["items"])


def test_dnd_off_when_not_gaming_opens_no_session(client):
    """Symmetric: clearing DND while merely available opens nothing."""
    client.post("/status/dnd", json={"enabled": True})
    client.post("/status/dnd", json={"enabled": False})
    client.post("/status", json={"state": "available"})
    assert _digests(client) == []
