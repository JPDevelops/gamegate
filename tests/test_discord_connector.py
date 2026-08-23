"""Discord connector logic with fakes — no gateway, no discord.py needed."""
from app.integrations.discord_connector import (
    DeliveryPump,
    classify_priority,
    format_notification,
    format_status_reply,
    normalize_message,
)


def test_priority_rules():
    assert classify_priority("this is URGENT please", is_dm=False) == "urgent"
    assert classify_priority("hey what's up", is_dm=True) == "actionable"
    assert classify_priority("channel chatter", is_dm=False) == "informational"


def test_normalize_message_shape():
    payload = normalize_message(
        "123", "juliann01", "general", "need this asap", "2026-08-23T00:00:00+00:00"
    )
    assert payload["source"] == "discord"
    assert payload["external_id"] == "123"
    assert payload["priority"] == "urgent"
    assert payload["metadata"]["channel"] == "general"


def test_normalized_message_ingests_into_api(client):
    payload = normalize_message(
        "456", "juliann01", "general", "hello", "2026-08-23T00:00:00+00:00"
    )
    assert client.post("/events", json=payload).status_code == 201
    # Replay of the same Discord message is idempotent.
    assert client.post("/events", json=payload).status_code == 200


class FakeApi:
    def __init__(self):
        self.notifications = [
            {"id": "n1", "event": {"source": "gmail", "sender": "boss", "title": "Call me"}}
        ]
        self.digests = [{"id": "d1", "text": "digest text"}]
        self.acked = []

    def pending_notifications(self):
        return list(self.notifications)

    def ack_notification(self, nid):
        self.acked.append(nid)
        self.notifications = [n for n in self.notifications if n["id"] != nid]
        return True

    def pending_digests(self):
        return list(self.digests)

    def ack_digest(self, did):
        self.acked.append(did)
        self.digests = [d for d in self.digests if d["id"] != did]
        return True


def test_delivery_pump_sends_then_acks():
    api = FakeApi()
    sent = []
    pump = DeliveryPump(api, lambda text: sent.append(text) or True)
    assert pump.run_once() == 2
    assert api.acked == ["n1", "d1"]
    assert any("Urgent while you play" in s for s in sent)
    assert pump.run_once() == 0  # queue drained — nothing re-sent


def test_delivery_pump_keeps_items_pending_when_discord_is_down():
    api = FakeApi()
    pump = DeliveryPump(api, lambda text: False)  # every send fails
    assert pump.run_once() == 0
    assert api.acked == []  # nothing acked → retried next cycle
    assert len(api.pending_notifications()) == 1


def test_status_reply_formats():
    assert "unreachable" in format_status_reply(None)
    assert "🎮" in format_status_reply({"state": "gaming", "application": "cs2.exe"})
    assert format_status_reply({"state": "focused"}) == "Status: focused"
    assert "boss" in format_notification(
        {"source": "gmail", "sender": "boss", "title": "Call me"}
    )
