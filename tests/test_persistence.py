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


def _digest_count(db) -> int:
    return db.connection().execute("SELECT COUNT(*) c FROM digests").fetchone()["c"]


def test_only_one_session_open_and_one_close_wins(client):
    """Through the REAL production path (StatusService): a repeated 'gaming' for
    the same app opens no second session, and one 'available' closes it exactly
    once, producing exactly one digest."""
    from app import db as db_module
    from app.deps import get_status_service
    from app.models.status import StatusUpdate

    db = db_module.get_database()
    svc = get_status_service()
    svc.set(StatusUpdate(state="gaming", application="Game"))
    svc.set(StatusUpdate(state="gaming", application="Game"))  # same app → no-op
    assert _open_session_count(db) == 1

    _, closed = svc.set(StatusUpdate(state="available"))
    assert closed is not None
    assert _open_session_count(db) == 0
    assert _digest_count(db) == 1


def test_concurrent_closes_only_one_wins(client):
    """Real race on the PRODUCTION path: N threads each drive
    StatusService.set(available) against the SAME open session at once — exactly
    what two overlapping '/status available' requests do. The product invariant
    is that exactly one session closes and exactly one recap is built. That is
    defended in depth by two independent mechanisms — the rowcount-gated close
    (`... AND ended_at IS NULL`) and the UNIQUE(session_id) index on digests — so
    a single dropped guard would not silently produce duplicate recaps. This
    test asserts the invariant itself on the real code path (the previous version
    raced SessionRepository.close_current(), which production never calls)."""
    import threading

    from app import db as db_module
    from app.deps import get_status_service
    from app.models.status import StatusUpdate

    db = db_module.get_database()
    get_status_service().set(StatusUpdate(state="gaming", application="Game"))

    n = 8
    ready = threading.Barrier(n)
    results: list = []
    lock = threading.Lock()

    def closer():
        svc = get_status_service()  # fresh repos → thread-local connections
        ready.wait()                # line all threads up so they truly race
        _, closed = svc.set(StatusUpdate(state="available"))
        with lock:
            results.append(closed)

    threads = [threading.Thread(target=closer) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r is not None]
    assert len(winners) == 1, f"expected exactly one close to win, got {len(winners)}"
    assert _open_session_count(db) == 0
    assert _digest_count(db) == 1  # the actual product invariant: one recap


def _open_session_count(db) -> int:
    return db.connection().execute(
        "SELECT COUNT(*) c FROM sessions WHERE ended_at IS NULL"
    ).fetchone()["c"]


def test_gaming_reopens_a_lost_session(client):
    """B1 self-heal: status=gaming with no open session (a crash between the
    status write and the session write) is repaired on the next poll. On the
    old transition-only code, a repeat 'gaming' was not 'entering' and nothing
    reopened."""
    from app import db as db_module

    db = db_module.get_database()
    client.post("/status", json={"state": "gaming", "application": "Rust"})
    # simulate the lost session (close it out-of-band, leaving status=gaming):
    with db.connection() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at = '2020-01-01T00:00:00+00:00'"
            " WHERE ended_at IS NULL"
        )
    assert _open_session_count(db) == 0
    client.post("/status", json={"state": "gaming", "application": "Rust"})  # same app
    assert _open_session_count(db) == 1  # reconciled → reopened


def test_available_closes_a_stuck_open_session(client):
    """B1 self-heal: a session left open after status already went non-gaming
    (crash between the status write and the close) is closed on the next poll."""
    from app import db as db_module

    db = db_module.get_database()
    client.post("/status", json={"state": "gaming", "application": "Rust"})
    # simulate stuck-open: flip status to available WITHOUT closing the session:
    with db.connection() as conn:
        conn.execute("UPDATE status SET state='available', application=NULL WHERE id=1")
    assert _open_session_count(db) == 1  # split-brain: available but session open
    client.post("/status", json={"state": "available"})
    assert _open_session_count(db) == 0  # reconciled → closed
