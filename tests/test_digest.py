"""End-to-end digest behavior: queueing during gaming, break-through
notifications, and exactly one digest per session."""
from tests.test_events import make_event


def start_gaming(client):
    client.post("/status", json={"state": "gaming", "application": "helldivers2.exe"})


def test_normal_events_queue_during_gaming(client):
    start_gaming(client)
    client.post("/events", json=make_event(external_id="q1", priority="actionable"))
    client.post("/events", json=make_event(external_id="q2", priority="informational"))

    preview = client.get("/digest").json()
    assert preview["total_events"] == 2
    assert client.get("/notifications/pending").json() == []


def test_urgent_breaks_through_during_gaming(client):
    start_gaming(client)
    client.post("/events", json=make_event(external_id="u1", priority="urgent"))

    pending = client.get("/notifications/pending").json()
    assert len(pending) == 1
    assert pending[0]["event"]["priority"] == "urgent"
    # Break-through events are consumed — they never show up in the digest too.
    assert client.get("/digest").json()["total_events"] == 0

    notification_id = pending[0]["id"]
    assert client.post(f"/notifications/{notification_id}/ack").status_code == 200
    assert client.get("/notifications/pending").json() == []
    # Acking twice is an error, not a silent success.
    assert client.post(f"/notifications/{notification_id}/ack").status_code == 404


def test_abandoned_notification_leaves_the_pending_queue(client):
    """Review MAJOR: a notification the client gave up on is dead-lettered, so it
    leaves the pending queue (can't wedge newer items) and can't be abandoned or
    acked twice."""
    start_gaming(client)
    client.post("/events", json=make_event(external_id="u9", priority="urgent"))
    nid = client.get("/notifications/pending").json()[0]["id"]

    assert client.post(f"/notifications/{nid}/abandon").status_code == 200
    assert client.get("/notifications/pending").json() == []   # left the queue
    # Already dead-lettered → abandon and ack both 404 now.
    assert client.post(f"/notifications/{nid}/abandon").status_code == 404
    assert client.post(f"/notifications/{nid}/ack").status_code == 404


def test_session_end_produces_exactly_one_digest(client):
    start_gaming(client)
    client.post("/events", json=make_event(external_id="d1", priority="actionable"))
    client.post("/events", json=make_event(external_id="d2", priority="informational"))
    client.post("/status", json={"state": "available"})

    latest = client.get("/digest/latest").json()
    assert latest["total_events"] == 2
    assert latest["session"]["application"] == "helldivers2.exe"
    assert "text" in latest

    # The queue was consumed: preview is empty, a second digest gets nothing.
    assert client.get("/digest").json()["total_events"] == 0
    start_gaming(client)
    client.post("/status", json={"state": "available"})
    assert client.get("/digest/latest").json()["total_events"] == 0

    # Both digests pending delivery, ack works once each.
    pending = client.get("/digests/pending").json()
    assert len(pending) == 2
    for digest in pending:
        assert client.post(f"/digests/{digest['id']}/ack").status_code == 200
    assert client.get("/digests/pending").json() == []


def test_suppressed_events_never_reach_digest(client):
    start_gaming(client)
    client.post("/events", json=make_event(external_id="s1", priority="ignore"))
    client.post("/status", json={"state": "available"})
    assert client.get("/digest/latest").json()["total_events"] == 0


def test_digest_is_deterministic(client):
    start_gaming(client)
    for ext_id, priority in [("a", "informational"), ("b", "urgent"), ("c", "actionable")]:
        client.post(
            "/events",
            json=make_event(external_id=ext_id, priority=priority, title=f"event-{ext_id}"),
        )
    # The urgent event broke through (delivered immediately), so the digest
    # holds actionable + informational, ordered by priority.
    client.post("/status", json={"state": "available"})
    latest = client.get("/digest/latest").json()
    titles = [item["title"] for item in latest["items"]]
    assert titles == ["event-c", "event-a"]

    # Actually prove determinism (the test name's claim): the pure builder called
    # twice on the same events yields byte-identical output — not just once.
    import json

    from app.models.event import Event
    from app.services.digest_service import build_digest

    events = [
        Event(source="gmail", external_id=f"d{i}", sender="s", title=f"t{i}",
              received_at=f"2026-08-24T00:00:0{i}+00:00", priority=p)
        for i, p in enumerate(["urgent", "actionable", "informational"])
    ]
    first = build_digest({"id": "x"}, events)
    second = build_digest({"id": "x"}, events)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_stale_events_never_interrupt(client):
    """Initial-sync flood (live bug, 2026-08-23): old messages queue for the
    digest even when routing would deliver them now."""
    from datetime import UTC, datetime, timedelta

    old = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    fresh = datetime.now(UTC).isoformat()
    # state = available → normally everything is deliver-now
    client.post("/events", json=make_event(external_id="old-1", received_at=old))
    client.post("/events", json=make_event(external_id="new-1", received_at=fresh))

    pending = client.get("/notifications/pending").json()
    assert [n["event"]["external_id"] for n in pending] == ["new-1"]
    assert client.get("/digest").json()["total_events"] == 1  # the stale one queued
