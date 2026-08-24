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


def test_naive_received_at_is_coerced_and_digest_does_not_500(client):
    """A naive received_at is normalized to UTC at the boundary, so mixing it
    with aware timestamps of the same priority never crashes digest sorting
    with a naive/aware TypeError -> 500 (M2)."""
    client.post("/status", json={"state": "gaming", "application": "g.exe"})
    # same priority, one NAIVE timestamp, one AWARE — these get sorted together
    client.post("/events", json=make_event(
        external_id="naive-1", priority="informational", requires_action=False,
        received_at="2020-01-01T00:00:00"))            # naive
    client.post("/events", json=make_event(
        external_id="aware-1", priority="informational", requires_action=False,
        received_at="2020-01-01T00:00:01+00:00"))      # aware
    end = client.post("/status", json={"state": "available"})  # builds the digest
    assert end.status_code == 200
    assert client.get("/digest").status_code == 200            # no 500


def test_unread_bulk_ids_are_capped(client):
    """The bulk-unread op caps its id list so a caller can't submit an
    unbounded batch (N5)."""
    assert client.post("/events/unread", json={"ids": ["x"] * 501}).status_code == 422
    assert client.post("/events/unread", json={"ids": ["x"] * 10}).status_code == 200


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
