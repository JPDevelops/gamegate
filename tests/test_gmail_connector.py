"""Gmail connector with fakes — the runbook's deterministic rules, proven."""
from app.integrations.gmail_connector import (
    GmailPoller,
    classify_email,
    normalize_email,
)

VIPS = ("boss@company.com",)


def test_vip_sender_is_urgent():
    assert classify_email("Boss <boss@company.com>", "quick question", "", VIPS) == "urgent"


def test_order_problem_is_actionable():
    assert (
        classify_email("customer@x.com", "My order has not arrived", "", VIPS)
        == "actionable"
    )


def test_newsletter_is_ignored():
    assert (
        classify_email("news@no-reply.shop.com", "Weekly deals", "unsubscribe here", VIPS)
        == "ignore"
    )


def test_default_is_informational():
    assert classify_email("friend@x.com", "lunch tomorrow?", "", VIPS) == "informational"


def test_vip_beats_newsletter_markers():
    # Rule order matters: a VIP hitting newsletter words is still urgent.
    assert (
        classify_email("boss@company.com", "newsletter draft", "unsubscribe", VIPS)
        == "urgent"
    )


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
    payload = normalize_email(make_message(), VIPS)
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
    poller = GmailPoller(FakeGmail([make_message(), make_message(id="gm-2")]), FakeApi(), VIPS)
    assert poller.poll_once() == 2


def test_gmail_outage_returns_zero_and_does_not_crash():
    poller = GmailPoller(FakeGmail(explode=True), FakeApi(), VIPS)
    assert poller.poll_once() == 0


def test_repolling_same_message_is_idempotent_end_to_end(client):
    """Full stack: same Gmail id polled twice → stored once."""
    payload = normalize_email(make_message(), VIPS)
    assert client.post("/events", json=payload).status_code == 201
    assert client.post("/events", json=payload).status_code == 200
    assert len(client.get("/events").json()) == 1
