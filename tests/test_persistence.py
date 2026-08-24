"""Step 4 acceptance: data survives restart, duplicates are idempotent,
gaming sessions are tracked."""
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from tests.test_events import make_event


def test_events_and_status_survive_restart(client, tmp_path):
    client.post("/events", json=make_event())
    client.post("/status", json={"state": "focused"})

    # Simulate a process restart: new Database object over the same file.
    db_module.init_database(str(tmp_path / "test.db"))
    with TestClient(app) as revived:
        assert len(revived.get("/events").json()) == 1
        assert revived.get("/status").json()["state"] == "focused"


def test_duplicate_external_event_is_idempotent(client):
    first = client.post("/events", json=make_event(external_id="dup-1"))
    replay = client.post("/events", json=make_event(external_id="dup-1", title="changed"))
    assert first.status_code == 201
    assert replay.status_code == 200  # replay returns the original, creates nothing
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["title"] == "Order has not arrived"
    assert len(client.get("/events").json()) == 1


def test_same_external_id_from_different_sources_is_not_a_duplicate(client):
    client.post("/events", json=make_event(source="gmail", external_id="42"))
    client.post("/events", json=make_event(source="slack", external_id="42"))
    assert len(client.get("/events").json()) == 2


def test_gaming_session_opened_and_closed(client):
    client.post("/status", json={"state": "gaming", "application": "helldivers2.exe"})
    conn = db_module.get_database().connection()
    open_sessions = conn.execute("SELECT * FROM sessions WHERE ended_at IS NULL").fetchall()
    assert len(open_sessions) == 1

    client.post("/status", json={"state": "available"})
    row = conn.execute("SELECT * FROM sessions").fetchone()
    assert row["ended_at"] is not None
    assert row["duration_seconds"] >= 0

    # A second non-gaming update must not close or create anything.
    client.post("/status", json={"state": "focused"})
    assert conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"] == 1


def test_only_one_session_open_and_one_close_wins(client):
    """Race fix: a second concurrent open is a no-op, and only the first close
    returns the session (so only one digest is ever built)."""
    from app import db as db_module
    from app.services.repositories import SessionRepository

    repo = SessionRepository(db_module.get_database())
    s1 = repo.open("Game", None)
    s2 = repo.open("Game", None)          # second open must be refused
    assert s1 is not None and s2 is None

    first = repo.close_current()
    second = repo.close_current()          # second close must lose the race
    assert first is not None and second is None
