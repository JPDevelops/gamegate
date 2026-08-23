def make_event(**overrides):
    from datetime import UTC, datetime

    event = {
        "source": "gmail",
        "external_id": "msg-001",
        "sender": "customer@example.com",
        "title": "Order has not arrived",
        "content": "My order from last week still isn't here.",
        # fresh by default: stale events are deliberately never delivered now
        "received_at": datetime.now(UTC).isoformat(),
        "priority": "actionable",
        "requires_action": True,
    }
    event.update(overrides)
    return event


def test_post_valid_event_returns_201_with_id(client):
    response = client.post("/events", json=make_event())
    assert response.status_code == 201
    body = response.json()
    assert body["id"]
    assert body["created_at"]
    assert body["source"] == "gmail"


def test_invalid_source_and_priority_are_rejected(client):
    assert client.post("/events", json=make_event(source="carrier-pigeon")).status_code == 422
    assert client.post("/events", json=make_event(priority="mega-urgent")).status_code == 422
    assert client.get("/events").json() == []


def test_events_listed_newest_first(client):
    client.post("/events", json=make_event(external_id="a", title="first"))
    client.post("/events", json=make_event(external_id="b", title="second"))
    titles = [e["title"] for e in client.get("/events").json()]
    assert titles == ["second", "first"]


def test_list_respects_limit(client):
    for i in range(5):
        client.post("/events", json=make_event(external_id=str(i)))
    assert len(client.get("/events", params={"limit": 3}).json()) == 3


def test_read_state_is_idempotent_and_reversible(client):
    event = client.post("/events", json=make_event(external_id="r1")).json()
    assert client.post(f"/events/{event['id']}/read").status_code == 200
    assert client.post(f"/events/{event['id']}/read").status_code == 200  # idempotent
    listed = client.get("/events").json()[0]
    assert listed["read_at"] is not None
    assert client.post(f"/events/{event['id']}/unread").status_code == 200
    assert client.get("/events").json()[0]["read_at"] is None
    assert client.post("/events/nope/read").status_code == 404


def test_read_all_and_bulk_undo(client):
    ids = [
        client.post("/events", json=make_event(external_id=f"ra{i}")).json()["id"]
        for i in range(3)
    ]
    marked = client.post("/events/read-all").json()["marked"]
    assert sorted(marked) == sorted(ids)
    assert all(e["read_at"] for e in client.get("/events").json())
    undone = client.post("/events/unread", json={"ids": marked}).json()
    assert undone == {"unmarked": 3}
    assert all(e["read_at"] is None for e in client.get("/events").json())


def test_read_state_never_affects_recaps(client):
    client.post("/status", json={"state": "gaming", "application": "g.exe"})
    event = client.post("/events", json=make_event(external_id="rr1", priority="actionable")).json()
    client.post(f"/events/{event['id']}/read")  # read it mid-session
    client.post("/status", json={"state": "available"})
    recap = client.get("/digest/latest").json()
    assert recap["total_events"] == 1  # read or not, it happened during the session
