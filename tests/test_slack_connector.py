"""Slack connector on fakes — normalization, retries, junk resistance."""
from app.integrations.slack_connector import (
    classify_slack,
    handle_slack_event,
    normalize_mention,
)


def mention_payload(text="hey <@U123> can you look at this?", ts="1755900000.000100"):
    return {
        "event": {
            "type": "app_mention",
            "user": "U777",
            "text": text,
            "channel": "C42",
            "event_ts": ts,
        }
    }


def test_mention_is_actionable_by_default():
    assert classify_slack("can you review my PR?") == "actionable"


def test_urgent_keywords_upgrade_priority():
    assert classify_slack("prod down, need you ASAP") == "urgent"


def test_normalize_mention_shape():
    payload = normalize_mention(mention_payload()["event"])
    assert payload["source"] == "slack"
    assert payload["external_id"] == "C42:1755900000.000100"
    assert payload["requires_action"] is True
    # The Slack ts 1755900000.000100 maps to a fixed instant — assert it exactly
    # instead of an "or 2026" branch that could never run (review NITPICK).
    assert payload["received_at"].startswith("2025-08-22T22:00:00")


def test_non_mention_events_are_ignored():
    assert normalize_mention({"type": "message", "text": "hi"}) is None
    assert handle_slack_event({"event": {"type": "reaction_added"}}, api=None) == "ignored"
    assert handle_slack_event({}, api=None) == "ignored"


class FakeApi:
    def __init__(self):
        self.posted = []

    def post_event(self, payload):
        self.posted.append(payload)
        return True


def test_handle_event_posts_normalized_payload():
    api = FakeApi()
    assert handle_slack_event(mention_payload(), api) == "ingested"
    assert api.posted[0]["source"] == "slack"


class DownApi:
    def post_event(self, payload):
        return False


def test_failed_ingestion_reports_failed_so_slack_redelivers():
    assert handle_slack_event(mention_payload(), DownApi()) == "failed"


def test_slack_retry_is_idempotent_end_to_end(client):
    """Slack redelivers the same event → the API stores it once."""
    payload = normalize_mention(mention_payload()["event"])
    assert client.post("/events", json=payload).status_code == 201
    assert client.post("/events", json=payload).status_code == 200
    assert len(client.get("/events").json()) == 1
