"""Gmail connector with fakes — the connector's deterministic rules. VIP-sender
handling lives server-side now (see test_ingest_vip), so the connector no
longer carries a VIP list (M14)."""
from app.integrations.gmail_connector import (
    GmailPoller,
    classify_email,
    normalize_email,
)


def test_order_problem_is_actionable():
    assert (
        classify_email("customer@x.com", "My order has not arrived", "")
        == "actionable"
    )


def test_newsletter_is_ignored():
    assert (
        classify_email("news@no-reply.shop.com", "Weekly deals", "unsubscribe here")
        == "ignore"
    )


def test_default_is_informational():
    assert classify_email("friend@x.com", "lunch tomorrow?", "") == "informational"


def make_message(**overrides):
    message = {
        "id": "gm-1",
        "sender": "customer@example.com",
        "subject": "Order delayed",
        "snippet": "Where is my package?",
        "received_at": "2026-08-23T01:00:00+00:00",
    }
    message.update(overrides)
    return message


def test_normalize_email_shape():
    payload = normalize_email(make_message())
    assert payload["source"] == "gmail"
    assert payload["external_id"] == "gm-1"
    assert payload["priority"] == "actionable"
    assert payload["requires_action"] is True
    assert "package" in payload["content"]


class FakeGmail:
    def __init__(self, messages=None, explode=False):
        self.messages = messages or []
        self.explode = explode

    def list_new_messages(self):
        if self.explode:
            raise ConnectionError("Gmail 503")
        return self.messages


class FakeApi:
    def __init__(self, up=True):
        self.up = up
        self.posted = []

    def post_event(self, payload):
        if not self.up:
            return False
        self.posted.append(payload)
        return True


def test_poll_once_ingests_all_messages():
    poller = GmailPoller(FakeGmail([make_message(), make_message(id="gm-2")]), FakeApi())
    assert poller.poll_once() == 2


def test_gmail_outage_returns_zero_and_does_not_crash():
    poller = GmailPoller(FakeGmail(explode=True), FakeApi())
    assert poller.poll_once() == 0


def test_repolling_same_message_is_idempotent_end_to_end(client):
    """Full stack: same Gmail id polled twice → stored once."""
    payload = normalize_email(make_message())
    assert client.post("/events", json=payload).status_code == 201
    assert client.post("/events", json=payload).status_code == 200
    assert len(client.get("/events").json()) == 1
