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
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

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

    def client_settings(self) -> dict | None:
        return self._request("GET", "/settings/client")


def notification_title_body(event: dict) -> tuple[str, str]:
    # Only actually-urgent events get the scary word (live find: an Amazon
    # shipping email was labeled 'Urgent' — the card must not cry wolf).
    label = "Urgent" if event.get("priority") == "urgent" else "New"
    return (
        f"{label} — {event.get('source', '?').upper()}",
        f"{event.get('sender', '?')}: {event.get('title', '')}",
    )


def digest_title_body(digest: dict) -> tuple[str, str]:
    text = digest.get("text", "Session digest ready.")
    lines = text.splitlines()
    return (lines[0] if lines else "GameGate digest", "\n".join(lines[1:6]))


class ToastPump:
    """Poll pending items → show toast → ack. Ack only after a successful
    show, so a failed/closed notifier never loses anything. User settings
    (sound, duration) are fetched from the server and applied only when the
    settings version changes (Orion: version-gated)."""

    def __init__(self, api: FullApiClient, show_fn) -> None:
        self.api = api
        self.show = show_fn
        self._settings_version = -1
        self.sound = True
        self.duration_s = 8

    def _apply_settings(self) -> None:
        settings = self.api.client_settings()
        if settings and settings.get("version", -1) != self._settings_version:
            self._settings_version = settings.get("version", -1)
            self.sound = bool(settings.get("notification_sound", True))
            self.duration_s = int(settings.get("overlay_duration_s", 8))

    def run_once(self) -> int:
        self._apply_settings()
        delivered = 0
        for notification in self.api.pending_notifications():
            title, body = notification_title_body(notification.get("event", {}))
            shown = self.show(title, body, duration_s=self.duration_s, sound=self.sound)
            if shown and self.api.ack_notification(notification["id"]):
                delivered += 1
        for digest in self.api.pending_digests():
            title, body = digest_title_body(digest)
            shown = self.show(title, body, duration_s=self.duration_s, sound=self.sound)
            if shown and self.api.ack_digest(digest["id"]):
                delivered += 1
        return delivered


class DndController:
    """Manual do-not-disturb: overrides state via the normal /status API.

    While DND is active the detector must not fight the override (it would
    re-post gaming/available on its next transition), so the tray pauses the
    detector and re-syncs it on release."""

    def __init__(self, api: ApiClient, detector=None) -> None:
        self.api = api
        self.detector = detector
        self.active = False

    def toggle(self) -> bool:
        if self.active:
            if self.api.post_status("available", None, None):
                self.active = False
                if self.detector is not None:
                    # Forget the last report so the next poll re-syncs the
                    # true state (e.g. a game that started during DND).
                    self.detector.last_reported_state = None
        else:
            if self.api.post_status("focused", None, None):
                self.active = True
        return self.active


def windows_toast(title: str, body: str, duration_s: int = 8, sound: bool = True) -> bool:
    """Native Windows toast (fallback notifier). Returns False on any
    failure so the pump retries instead of acking."""
    try:
        from winotify import Notification

        Notification(app_id="GameGate", title=title, msg=body or " ").show()
        return True
    except Exception:
        log.exception("Toast failed")
        return False


def build_window_url(config: dict) -> str:
    """Dashboard URL for the desktop window; the key logs the webview in
    once, after which the HttpOnly cookie takes over."""
    base = config["api_url"].rstrip("/")
    token = config.get("api_token", "")
    return f"{base}/app?key={token}" if token else f"{base}/app"


def open_window() -> None:
    """Launch the GameGate window as a separate process, so closing it never
    touches the tray/detector and the tray's single-instance lock stays clean."""
    if getattr(sys, "frozen", False):
        subprocess.Popen([sys.executable, "--window"])
    else:
        subprocess.Popen([sys.executable, __file__, "--window"])


def _darken_titlebar(window) -> None:
    """Native frame, dark trim: paint the Windows title bar in the app's own
    colors via DWM. Restores everything frameless broke (Aero Snap, Win+arrow,
    maximize) while keeping the chrome dark — Jules' both requirements."""
    try:
        import ctypes

        handle = window.native.Handle
        hwnd = handle.ToInt32() if hasattr(handle, "ToInt32") else int(handle)
        dwm = ctypes.windll.dwmapi
        one = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (19 on older builds)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(one), ctypes.sizeof(one))
        # Win11: exact caption + text colors (COLORREF is 0x00BBGGRR)
        caption = ctypes.c_uint(0x00161110)  # our sidebar color #101116
        text = ctypes.c_uint(0x00EFE8E6)     # our text color #e6e8ef
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))
    except Exception:  # noqa: BLE001 — cosmetic; never block the window
        log.debug("Dark titlebar not applied (pre-Win10 or non-Windows)")


def update_script_path() -> Path:
    """agent/update.ps1, whether we're the frozen exe (agent/dist/GameGate.exe)
    or running from source (agent/tray_app.py)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent.parent / "update.ps1"
    return Path(__file__).parent / "update.ps1"


def launch_updater() -> bool:
    """Spawn the updater in its own console; caller must quit so the exe
    unlocks. Returns False when the script is missing (e.g. moved exe)."""
    script = update_script_path()
    if not script.exists():
        log.error("Updater script not found at %s", script)
        return False
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        creationflags=creationflags,
    )
    return True


def run_window() -> None:
    """The desktop window: NATIVE frame (snap/maximize/Win+arrow all work)
    with the title bar painted in app colors — Edge WebView2 inside."""
    import webview

    config = load_config()
    window = webview.create_window(
        "GameGate", build_window_url(config),
        width=1080, height=760, background_color="#0f1014",
    )
    webview.start(_darken_titlebar, window)


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
    dnd = DndController(api, detector)
    stop = threading.Event()

    icons = {state: render_badge(state) for state in ("available", "gaming", "focused")}

    def detector_loop():
        while not stop.is_set():
            try:
                if not dnd.active:  # paused during manual do-not-disturb
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
        notify("GameGate status", str(state), duration_s=pump.duration_s, sound=pump.sound)

    def on_digest(icon, _item):
        digest = api.latest_digest()
        if digest:
            title, body = digest_title_body(digest)
            notify(title, body, duration_s=pump.duration_s, sound=pump.sound)
        else:
            notify("GameGate", "No digest yet.", duration_s=pump.duration_s, sound=pump.sound)

    def on_dnd(icon, _item):
        active = dnd.toggle()
        notify(
            "GameGate", "Do Not Disturb ON" if active else "Back to available",
            duration_s=pump.duration_s, sound=pump.sound,
        )

    def on_update(icon, _item):
        notify(
            "GameGate", "Updating — the app will restart itself in about a minute.",
            duration_s=pump.duration_s, sound=False,
        )
        if launch_updater():
            stop.set()
            icon.stop()  # quit so the exe unlocks for the rebuild
        else:
            notify("GameGate", "Updater script not found — update from the repo folder.",
                   duration_s=pump.duration_s, sound=False)

    def on_quit(icon, _item):
        stop.set()
        icon.stop()

    def on_open(icon, _item):
        open_window()

    icon = pystray.Icon(
        "GameGate", icons["available"], "GameGate",
        menu=pystray.Menu(
            pystray.MenuItem("Open GameGate", on_open, default=True),
            pystray.MenuItem("Status", on_status),
            pystray.MenuItem("Last digest", on_digest),
            pystray.MenuItem("Do Not Disturb", on_dnd),
            pystray.MenuItem("Update GameGate", on_update),
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
    if "--window" in sys.argv:
        run_window()  # window process: no tray, no lock — the tray owns those
        raise SystemExit(0)
    _lock = acquire_single_instance_lock()
    if _lock is None:
        log.error("GameGate is already running — exiting.")
        raise SystemExit(1)
    run_tray()
