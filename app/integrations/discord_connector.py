"""Discord connector logic — everything testable WITHOUT discord.py.

Three responsibilities:
1. normalize_message: Discord message → internal EventIn payload
2. GameGateApi: the connector's HTTP client for the core service
3. DeliveryPump: polls pending digests/notifications and pushes them to a
   channel via an injected send function

The actual gateway wiring (discord.py) lives in discord_bot.py and stays thin.
"""
import logging

import httpx

log = logging.getLogger("gamegate.discord")

URGENT_KEYWORDS = ("urgent", "asap", "emergency", "911")


def classify_priority(content: str, is_dm: bool) -> str:
    lowered = content.lower()
    if any(keyword in lowered for keyword in URGENT_KEYWORDS):
        return "urgent"
    if is_dm:
        return "actionable"
    return "informational"


def normalize_message(
    message_id: str,
    author: str,
    channel: str,
    content: str,
    created_at_iso: str,
    is_dm: bool = False,
) -> dict:
    return {
        "source": "discord",
        "external_id": message_id,
        "sender": author,
        "title": f"Message in #{channel}" if not is_dm else "Direct message",
        "content": content[:2000],
        "received_at": created_at_iso,
        "priority": classify_priority(content, is_dm),
        "requires_action": is_dm,
        "metadata": {"channel": channel},
    }


class GameGateApi:
    def __init__(self, base_url: str, token: str = "", client: httpx.Client | None = None):
        headers = {"X-GameGate-Token": token} if token else {}
        self.client = client or httpx.Client(
            base_url=base_url, headers=headers, timeout=10
        )

    def post_event(self, payload: dict) -> bool:
        try:
            response = self.client.post("/events", json=payload)
            return response.status_code in (200, 201)
        except httpx.HTTPError as exc:
            log.warning("post_event failed: %s", exc)
            return False

    def get_status(self) -> dict | None:
        return self._get_json("/status")

    def get_digest_preview(self) -> dict | None:
        return self._get_json("/digest")

    def pending_digests(self) -> list[dict]:
        return self._get_json("/digests/pending") or []

    def ack_digest(self, digest_id: str) -> bool:
        return self._post_ok(f"/digests/{digest_id}/ack")

    def pending_notifications(self) -> list[dict]:
        return self._get_json("/notifications/pending") or []

    def ack_notification(self, notification_id: str) -> bool:
        return self._post_ok(f"/notifications/{notification_id}/ack")

    def heartbeat(self, name: str, ok: bool, detail: str | None = None) -> bool:
        """Best-effort liveness report; never raises so it can't crash a poll."""
        try:
            return self.client.post(
                f"/connectors/{name}/heartbeat", json={"ok": ok, "detail": detail}
            ).status_code == 200
        except httpx.HTTPError as exc:
            log.warning("heartbeat %s failed: %s", name, exc)
            return False

    def _get_json(self, path: str):
        try:
            response = self.client.get(path)
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError as exc:
            log.warning("GET %s failed: %s", path, exc)
        return None

    def _post_ok(self, path: str) -> bool:
        try:
            return self.client.post(path).status_code == 200
        except httpx.HTTPError as exc:
            log.warning("POST %s failed: %s", path, exc)
            return False


def format_status_reply(status: dict | None) -> str:
    if status is None:
        return "GameGate API is unreachable right now."
    state = status["state"]
    if state == "gaming":
        return (
            f"🎮 Gaming — {status.get('application') or 'unknown game'}. "
            "Non-urgent messages are being held."
        )
    return f"Status: {state}"


def format_notification(event: dict) -> str:
    return (
        f"🚨 **Urgent while you play** — [{event['source'].upper()}] "
        f"{event['sender']}: {event['title']}"
    )


class DeliveryPump:
    """Pushes pending digests and break-through notifications to Discord.

    Delivery is at-least-once. If Discord is down, an item stays pending and is
    retried next cycle. If a send SUCCEEDS but the follow-up ack fails, we must
    NOT re-post to Discord every cycle — so a per-process 'already sent' set
    means we only retry the ack, not the send (review MAJOR: resend storm). A
    process restart between send and ack can still re-post once (the honest
    limit of at-least-once without a persisted outbound id)."""

    def __init__(self, api: GameGateApi, send_fn) -> None:
        self.api = api
        self.send = send_fn
        self._sent_notifications: set = set()
        self._sent_digests: set = set()

    def run_once(self) -> int:
        delivered = 0
        for notification in self.api.pending_notifications():
            nid = notification["id"]
            if nid not in self._sent_notifications:
                if not self.send(format_notification(notification["event"])):
                    continue  # send failed — retry the whole item next cycle
                self._sent_notifications.add(nid)   # sent once; only ack may retry
            if self.api.ack_notification(nid):
                self._sent_notifications.discard(nid)
                delivered += 1
        for digest in self.api.pending_digests():
            did = digest["id"]
            if did not in self._sent_digests:
                if not self.send(digest.get("text", "Digest ready.")):
                    continue
                self._sent_digests.add(did)
            if self.api.ack_digest(did):
                self._sent_digests.discard(did)
                delivered += 1
        return delivered
