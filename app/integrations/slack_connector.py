"""Slack connector — Socket Mode, app_mention first (Step 8).

Socket Mode keeps everything outbound (no public webhook URL). Slack retries
deliveries, so the same event can arrive more than once; external_id is built
from channel + event_ts, and the API's (source, external_id) idempotency
absorbs the replays. Real Slack wiring is lazy-imported and disabled unless
SLACK_ENABLED=true — CI and tests use fakes only.
"""
import logging
import os

log = logging.getLogger("gamegate.slack")

URGENT_KEYWORDS = ("urgent", "asap", "emergency", "prod down", "outage")


def classify_slack(text: str) -> str:
    lowered = text.lower()
    if any(keyword in lowered for keyword in URGENT_KEYWORDS):
        return "urgent"
    # A direct mention is a request for this user's attention.
    return "actionable"


def normalize_mention(event: dict) -> dict | None:
    """app_mention Slack event → internal event payload. Returns None for
    event types we don't ingest (this connector starts with mentions only)."""
    if event.get("type") != "app_mention":
        return None
    channel = event.get("channel", "unknown")
    event_ts = event.get("event_ts") or event.get("ts", "")
    text = event.get("text", "")
    priority = classify_slack(text)
    return {
        "source": "slack",
        "external_id": f"{channel}:{event_ts}",
        "sender": event.get("user", "unknown"),
        "title": f"Mention in <#{channel}>",
        "content": text[:2000],
        "received_at": _ts_to_iso(event_ts),
        "priority": priority,
        "requires_action": True,
        "metadata": {"channel": channel},
    }


def _ts_to_iso(event_ts: str) -> str:
    from datetime import UTC, datetime

    try:
        return datetime.fromtimestamp(float(event_ts), tz=UTC).isoformat()
    except (TypeError, ValueError):
        return datetime.now(UTC).isoformat()


def handle_slack_event(payload: dict, api) -> bool:
    """Process one Events API envelope body. Safe on junk: unknown shapes
    are ignored, API failures are logged and absorbed (Slack will retry)."""
    event = payload.get("event", {})
    normalized = normalize_mention(event)
    if normalized is None:
        return False
    try:
        return api.post_event(normalized)
    except Exception:
        log.exception("Slack event ingestion failed")
        return False


def run_socket_mode(api) -> None:
    """Live Socket Mode loop. Requires SLACK_ENABLED=true, SLACK_BOT_TOKEN
    (xoxb-) and SLACK_APP_TOKEN (xapp-, Socket Mode enabled)."""
    if os.environ.get("SLACK_ENABLED", "").lower() != "true":
        log.info("Slack connector disabled (SLACK_ENABLED != true)")
        return

    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse
    from slack_sdk.web import WebClient

    client = SocketModeClient(
        app_token=os.environ["SLACK_APP_TOKEN"],
        web_client=WebClient(token=os.environ["SLACK_BOT_TOKEN"]),
    )

    def _listener(sm_client: SocketModeClient, request: SocketModeRequest) -> None:
        # Ack immediately: Slack treats slow acks as failures and re-sends.
        sm_client.send_socket_mode_response(
            SocketModeResponse(envelope_id=request.envelope_id)
        )
        if request.type == "events_api":
            handle_slack_event(request.payload, api)

    client.socket_mode_request_listeners.append(_listener)
    client.connect()
    import threading

    threading.Event().wait()  # keep the process alive; client runs on threads
