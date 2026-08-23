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
