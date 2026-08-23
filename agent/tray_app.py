"""GameGate desktop app — tray icon + native Windows toast notifications.

THE notification surface (PO decision 2026-08-23: Discord is not the
notifier). Runs on the gaming PC alongside the detector, which it starts in a
background thread. Polls the GameGate API for pending break-through
notifications and digests, shows them as Windows toasts, and acks AFTER a
successful show — same send-then-ack reliability contract as every other
delivery path: if the app is closed, items queue server-side and arrive when
it's back.

Run:       python tray_app.py             (uses agent/config.json)
Package:   see docs/DESKTOP_APP.md (PyInstaller one-file GameGate.exe)

Windows-only bits (pystray, winotify) are lazy-imported so the pump logic
stays unit-testable on any OS.
"""
import json
import logging
import signal
import socket
import threading
import urllib.error
import urllib.request

from branding import render_badge
from detector import ApiClient, Detector, load_config, psutil_process_lister
from overlay import enable_dpi_awareness, show_overlay

SINGLE_INSTANCE_PORT = 47653  # arbitrary fixed port; second launch fails the bind


def acquire_single_instance_lock() -> socket.socket | None:
    """Bind a localhost port as a cross-process mutex. Returns the held
    socket, or None if another GameGate instance already owns it (issue #33:
    multiple instances caused ghost trays and un-quittable icons)."""
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        return lock
    except OSError:
        lock.close()
        return None

log = logging.getLogger("gamegate.tray")

POLL_SECONDS = 10


class FullApiClient(ApiClient):
    """Detector's client + the read/ack endpoints the notifier needs."""

    def _request(self, method: str, path: str):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-GameGate-Token"] = self.token
        request = urllib.request.Request(
            f"{self.base_url}{path}", headers=headers, method=method,
            data=b"{}" if method == "POST" else None,
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read() or "null")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            log.warning("%s %s failed: %s", method, path, exc)
            return None

    def pending_notifications(self) -> list:
        return self._request("GET", "/notifications/pending") or []

    def ack_notification(self, notification_id: str) -> bool:
        return self._request("POST", f"/notifications/{notification_id}/ack") is not None

    def pending_digests(self) -> list:
        return self._request("GET", "/digests/pending") or []

    def ack_digest(self, digest_id: str) -> bool:
        return self._request("POST", f"/digests/{digest_id}/ack") is not None

    def get_status(self) -> dict | None:
        return self._request("GET", "/status")

    def latest_digest(self) -> dict | None:
        return self._request("GET", "/digest/latest")


def notification_title_body(event: dict) -> tuple[str, str]:
    return (
        f"Urgent — {event.get('source', '?').upper()}",
        f"{event.get('sender', '?')}: {event.get('title', '')}",
    )


def digest_title_body(digest: dict) -> tuple[str, str]:
    text = digest.get("text", "Session digest ready.")
    lines = text.splitlines()
    return (lines[0] if lines else "GameGate digest", "\n".join(lines[1:6]))


class ToastPump:
    """Poll pending items → show toast → ack. Ack only after a successful
    show, so a failed/closed notifier never loses anything."""

    def __init__(self, api: FullApiClient, show_fn) -> None:
        self.api = api
        self.show = show_fn

    def run_once(self) -> int:
        delivered = 0
        for notification in self.api.pending_notifications():
            title, body = notification_title_body(notification.get("event", {}))
            if self.show(title, body) and self.api.ack_notification(notification["id"]):
                delivered += 1
        for digest in self.api.pending_digests():
            title, body = digest_title_body(digest)
            if self.show(title, body) and self.api.ack_digest(digest["id"]):
                delivered += 1
        return delivered


class DndController:
    """Manual do-not-disturb: overrides state via the normal /status API."""

    def __init__(self, api: ApiClient) -> None:
        self.api = api
        self.active = False

    def toggle(self) -> bool:
        if self.active:
            if self.api.post_status("available", None, None):
                self.active = False
        else:
            if self.api.post_status("focused", None, None):
                self.active = True
        return self.active


def windows_toast(title: str, body: str) -> bool:
    """Native Windows toast (fallback notifier). Returns False on any
    failure so the pump retries instead of acking."""
    try:
        from winotify import Notification

        Notification(app_id="GameGate", title=title, msg=body or " ").show()
        return True
    except Exception:
        log.exception("Toast failed")
        return False


def pick_notifier(config: dict):
    """Jules' spec: overlay (top-right box + sound) is the default; native
    toasts remain available via config {"notifier": "toast"} — note Focus
    Assist suppresses toasts during fullscreen gaming."""
    if config.get("notifier", "overlay") == "toast":
        return windows_toast
    return show_overlay


def run_tray() -> None:
    """Wire the tray icon, detector thread, and toast pump together."""
    import pystray

    config = load_config()
    api = FullApiClient(config["api_url"], config["api_token"])
    detector = Detector(config, psutil_process_lister, api)
    notify = pick_notifier(config)
    pump = ToastPump(api, notify)
    dnd = DndController(api)
    stop = threading.Event()

    icons = {state: render_badge(state) for state in ("available", "gaming", "focused")}

    def detector_loop():
        while not stop.is_set():
            try:
                detector.poll_once()
            except Exception:
                log.exception("Detector poll failed")
            stop.wait(config["poll_interval_seconds"])

    def pump_loop(icon: "pystray.Icon"):
        while not stop.is_set():
            try:
                pump.run_once()
                status = api.get_status()
                if status:
                    icon.icon = icons.get(status["state"], icons["available"])
            except Exception:
                log.exception("Pump cycle failed")
            stop.wait(POLL_SECONDS)

    def on_status(icon, _item):
        status = api.get_status()
        state = status["state"] if status else "API unreachable"
        notify("GameGate status", str(state))

    def on_digest(icon, _item):
        digest = api.latest_digest()
        if digest:
            notify(*digest_title_body(digest))
        else:
            notify("GameGate", "No digest yet.")

    def on_dnd(icon, _item):
        active = dnd.toggle()
        notify("GameGate", "Do Not Disturb ON" if active else "Back to available")

    def on_quit(icon, _item):
        stop.set()
        icon.stop()

    icon = pystray.Icon(
        "GameGate", icons["available"], "GameGate",
        menu=pystray.Menu(
            pystray.MenuItem("Status", on_status),
            pystray.MenuItem("Last digest", on_digest),
            pystray.MenuItem("Do Not Disturb", on_dnd),
            pystray.MenuItem("Quit", on_quit),
        ),
    )
    # Ctrl+C in the console stops everything cleanly (issue #33).
    def handle_sigint(_sig, _frame):
        stop.set()
        icon.stop()

    signal.signal(signal.SIGINT, handle_sigint)

    threading.Thread(target=detector_loop, daemon=True).start()
    threading.Thread(target=pump_loop, args=(icon,), daemon=True).start()
    icon.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    enable_dpi_awareness()
    _lock = acquire_single_instance_lock()
    if _lock is None:
        log.error("GameGate is already running — exiting.")
        raise SystemExit(1)
    run_tray()
