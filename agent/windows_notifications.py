"""Capture Windows notifications (Discord, Slack, email, anything) and feed them
into GameGate, so GameGate can hold / prioritize / recap EVERY notification on
the PC — not just the services it has a bot or OAuth for.

Windows-only: uses the OS UserNotificationListener API via the `winsdk` package,
which needs a one-time user permission grant (Settings > Privacy & security >
Notifications, plus the in-app consent prompt). Everything Windows-specific is
lazy-imported and guarded, so importing this module is safe on any OS and the
pure mapping helpers below are unit-tested on Linux.
"""
import logging

log = logging.getLogger("gamegate.winnotif")

URGENT_KEYWORDS = ("urgent", "asap", "emergency", "911", "help now")

# GameGate's OWN overlay/toast shows up in the Windows notification center too;
# we must never re-ingest it or the app would notify itself in an endless loop.
_SELF_APP_MARKERS = ("gamegate",)

# Map a source app onto one of GameGate's known EventSource values; anything
# unrecognized is "system" (a generic captured notification).
_APP_SOURCE = {
    "discord": "discord",
    "slack": "slack",
    "gmail": "gmail",
    "mail": "gmail",
    "outlook": "gmail",
}


def classify(app_name: str, title: str, body: str) -> tuple[str, str]:
    """(source, priority) for a captured notification — pure and testable.
    Priority is a first guess; the server still applies VIP/keyword rules."""
    name = (app_name or "").lower()
    source = "system"
    for marker, src in _APP_SOURCE.items():
        if marker in name:
            source = src
            break
    text = f"{title or ''} {body or ''}".lower()
    priority = "urgent" if any(k in text for k in URGENT_KEYWORDS) else "informational"
    return source, priority


def map_notification_to_event(
    app_name: str, title: str, body: str, notif_id: str, received_iso: str
) -> dict | None:
    """Turn a captured Windows notification into a GameGate /events payload, or
    return None if it should be skipped (our own toasts, or an empty one). Pure
    and testable — the Windows plumbing lives in WindowsNotificationListener."""
    if not app_name:
        return None
    if any(marker in app_name.lower() for marker in _SELF_APP_MARKERS):
        return None  # never re-ingest GameGate's own notification (would loop)
    title = (title or "").strip()
    body = (body or "").strip()
    if not title and not body:
        return None
    source, priority = classify(app_name, title, body)
    return {
        "source": source,
        "external_id": f"win-{notif_id}",   # stable id → idempotent across polls
        "sender": app_name,
        "title": (title or app_name)[:200],
        "content": body[:2000],
        "received_at": received_iso,
        "priority": priority,
        "requires_action": False,
        "metadata": {"origin": "windows-notification", "app": app_name},
    }


def _read_text(user_notification) -> tuple[str, str]:
    """Pull (title, body) out of a UserNotification's toast binding text lines."""
    try:
        binding = user_notification.notification.visual.bindings[0]
        texts = [e.text for e in binding.get_text_elements()]
    except Exception:  # noqa: BLE001 — any shape we don't recognize → empty
        return "", ""
    if not texts:
        return "", ""
    return texts[0], "\n".join(texts[1:])


class WindowsNotificationListener:
    """Polls the Windows notification center and posts each NEW notification to
    GameGate as an event. Call run() from a background thread. On a non-Windows
    box or without winsdk it logs once and returns — it never crashes the app."""

    def __init__(self, post_event_fn, poll_seconds: int = 4) -> None:
        self.post_event = post_event_fn
        self.poll_seconds = poll_seconds
        self._seen: set[str] = set()

    def run(self, stop) -> None:
        try:
            listener = self._request_access()
        except Exception:  # noqa: BLE001 — winsdk missing / not Windows / COM error
            log.exception(
                "Windows notification capture unavailable (is winsdk installed and "
                "are you on Windows?) — feature stays off"
            )
            return
        if listener is None:
            return
        log.info("Windows notification capture started")
        while not stop.is_set():
            try:
                self._poll_once(listener)
            except Exception:  # noqa: BLE001 — one bad poll must not kill the loop
                log.exception("Notification poll failed; continuing")
            stop.wait(self.poll_seconds)

    def _request_access(self):
        """Ask Windows for notification-listener access. Returns the listener or
        None if the user hasn't granted it."""
        from winsdk.windows.ui.notifications.management import (
            UserNotificationListener,
            UserNotificationListenerAccessStatus,
        )

        listener = UserNotificationListener.get_current()
        status = _run_async(listener.request_access_async())
        if status != UserNotificationListenerAccessStatus.ALLOWED:
            log.error(
                "Notification access not granted (status=%s). Turn it on under "
                "Settings > Privacy & security > Notifications and reopen GameGate.",
                status,
            )
            return None
        return listener

    def _poll_once(self, listener) -> None:
        from datetime import UTC, datetime

        from winsdk.windows.ui.notifications import NotificationKinds

        notifications = _run_async(listener.get_notifications_async(NotificationKinds.TOAST))
        for un in notifications:
            nid = str(un.id)
            if nid in self._seen:
                continue
            self._seen.add(nid)
            app_name = ""
            try:
                app_name = un.app_info.display_info.display_name or ""
            except Exception:  # noqa: BLE001 — some notifications lack app info
                app_name = ""
            title, body = _read_text(un)
            payload = map_notification_to_event(
                app_name, title, body, nid, datetime.now(UTC).isoformat()
            )
            if payload and self.post_event(payload):
                log.info("Captured notification from %s", app_name)
        # Bound the dedup set so a long-running session can't grow it forever.
        if len(self._seen) > 5000:
            self._seen = set(list(self._seen)[-2000:])


def _run_async(op):
    """Await a winsdk IAsyncOperation from sync code by driving a private loop."""
    import asyncio

    async def _await():
        return await op

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_await())
    finally:
        loop.close()
