"""GameGate desktop app — tray icon + break-through notification surface.

The desktop app is the notification surface (not Discord; PO decision
2026-08-23). It shows break-throughs via a Tkinter overlay by default —
Focus-Assist-immune, so it reaches you mid-game — and can use native Windows
toasts instead when config sets `"notifier": "toast"`. Runs on the gaming PC
alongside the detector, which it starts in a background thread. Polls the
GameGate API for pending break-through notifications and digests, shows each,
and acks AFTER a successful show — same send-then-ack reliability contract as
every other delivery path: if the app is closed, items queue server-side and
arrive when it's back.

Run:       python tray_app.py             (uses agent/config.json)
Package:   see docs/DESKTOP_APP.md (PyInstaller one-file GameGate.exe)

Windows-only bits (pystray, winotify) are lazy-imported so the pump logic
stays unit-testable on any OS.
"""
import contextlib
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

from branding import render_badge
from detector import ApiClient, Detector, app_dir, load_config, psutil_process_lister
from overlay import enable_dpi_awareness, show_overlay, show_update_prompt

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
        except urllib.error.HTTPError as exc:
            # Catch HTTPError before URLError (it's a subclass) so a 401 isn't
            # silently swallowed as "no notifications" — a wrong token would
            # otherwise make the desktop app show nothing forever (M11).
            if exc.code == 401:
                log.error("%s %s: token rejected (401) — check api_token in config.json",
                          method, path)
            else:
                log.warning("%s %s: HTTP %s", method, path, exc.code)
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            log.warning("%s %s failed: %s", method, path, exc)
            return None

    def pending_notifications(self) -> list:
        return self._request("GET", "/notifications/pending") or []

    def ack_notification(self, notification_id: str) -> bool:
        return self._request("POST", f"/notifications/{notification_id}/ack") is not None

    def abandon_notification(self, notification_id: str) -> bool:
        return self._request("POST", f"/notifications/{notification_id}/abandon") is not None

    def pending_digests(self) -> list:
        return self._request("GET", "/digests/pending") or []

    def ack_digest(self, digest_id: str) -> bool:
        return self._request("POST", f"/digests/{digest_id}/ack") is not None

    def abandon_digest(self, digest_id: str) -> bool:
        return self._request("POST", f"/digests/{digest_id}/abandon") is not None

    def get_status(self) -> dict | None:
        return self._request("GET", "/status")

    def latest_digest(self) -> dict | None:
        return self._request("GET", "/digest/latest")

    def client_settings(self) -> dict | None:
        return self._request("GET", "/settings/client")

    def post_event(self, payload: dict) -> bool:
        """Ingest a captured event (e.g. a Windows notification) into GameGate."""
        return self._post_json("/events", payload)

    def report_update_status(self, count: int, build: str) -> bool:
        """Tell the server how many updates are pending + this build, so the
        dashboard Settings area can show the same 'Latest version' / 'Update
        available' state as the tray (review: same function in settings)."""
        return self._post_json(
            "/agent/update-status", {"pending": int(count), "build": build}
        )


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
    settings version changes (version-gated)."""

    MAX_SHOW_ATTEMPTS = 3

    def __init__(self, api: FullApiClient, show_fn) -> None:
        self.api = api
        self.show = show_fn
        self._settings_version = -1
        self.sound = True
        self.duration_s = 8
        self._fail_counts: dict[str, int] = {}  # id -> consecutive failures
        self._shown: set[str] = set()            # rendered once; never re-render
        self._given_up: set[str] = set()         # retries exhausted; fully ignore

    def _show_or_drop(self, item_id: str, title: str, body: str, ack, abandon=None) -> bool:
        """Render an item AT MOST ONCE, then ack. If display OR ack keeps
        failing, give up after MAX_SHOW_ATTEMPTS so a poison item (e.g. an ack
        that 401s after a token rotation) can't re-render the same card every
        10s forever (review B1). On give-up we also tell the SERVER to
        dead-letter it (abandon), so 200 poison items can't wedge newer ones out
        of the oldest-200 pending window. A shown-but-unacked item is retried
        only at the ack layer — never re-displayed."""
        if item_id in self._given_up:
            return False
        if item_id not in self._shown:
            if not self.show(title, body, duration_s=self.duration_s, sound=self.sound):
                self._bump_fail(item_id, abandon)     # display failed
                return False
            self._shown.add(item_id)              # rendered exactly once
        if ack(item_id):
            self._shown.discard(item_id)
            self._fail_counts.pop(item_id, None)
            return True
        self._bump_fail(item_id, abandon)             # ack failed — count it too (B1)
        return False

    def _bump_fail(self, item_id: str, abandon=None) -> None:
        self._fail_counts[item_id] = self._fail_counts.get(item_id, 0) + 1
        if self._fail_counts[item_id] >= self.MAX_SHOW_ATTEMPTS:
            log.warning(
                "Giving up on %s after %d attempts; dead-lettering it server-side "
                "so it won't be re-shown or block newer items",
                item_id, self._fail_counts[item_id]
            )
            self._given_up.add(item_id)
            if abandon is not None:
                try:
                    abandon(item_id)   # server dead-letters it → leaves pending queue
                except Exception:      # noqa: BLE001 — never let cleanup crash the pump
                    log.exception("Failed to abandon %s server-side", item_id)

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
            nid = notification.get("id")
            if nid is None:  # a malformed server row must not abort the whole cycle
                continue
            title, body = notification_title_body(notification.get("event", {}))
            if self._show_or_drop(
                nid, title, body, self.api.ack_notification, self.api.abandon_notification
            ):
                delivered += 1
        for digest in self.api.pending_digests():
            did = digest.get("id")
            if did is None:
                continue
            title, body = digest_title_body(digest)
            if self._show_or_drop(
                did, title, body, self.api.ack_digest, self.api.abandon_digest
            ):
                delivered += 1
        return delivered


class DndController:
    """Manual do-not-disturb via the dashboard-authoritative override endpoint
    (POST /status/dnd), the SAME mechanism the web dashboard uses — so the two
    DND surfaces agree. The server holds the override and won't let a detector
    poll clobber it, and it re-opens the session itself when DND clears while
    still gaming, so the tray no longer has to pause the detector or fiddle with
    its last_reported_state (review: two divergent DND mechanisms)."""

    def __init__(self, api: ApiClient, detector=None) -> None:
        self.api = api
        self.detector = detector  # kept for signature compatibility; unused now
        self.active = False

    def toggle(self) -> bool:
        if self.api.set_dnd(not self.active):
            self.active = not self.active
        return self.active


def windows_toast(title: str, body: str, duration_s: int = 8, sound: bool = True) -> bool:
    """Native Windows toast (fallback notifier). Honors the user's duration and
    sound settings — winotify exposes both (review M2). Returns False on any
    failure so the pump retries instead of acking."""
    try:
        from winotify import Notification, audio

        # winotify duration is coarse ("short" ~5s / "long" ~25s); map the
        # user's seconds onto it rather than ignoring the setting.
        note = Notification(
            app_id="GameGate", title=title, msg=body or " ",
            duration="long" if duration_s > 8 else "short",
        )
        note.set_audio(audio.Default, loop=False) if sound else note.set_audio(
            audio.Silent, loop=False
        )
        note.show()
        return True
    except Exception:
        log.exception("Toast failed")
        return False


def _child_env() -> dict:
    """Environment for child processes, scrubbed of PyInstaller's onefile
    bootloader vars. Without this, a frozen GameGate.exe spawning a DIFFERENT
    executable trips 'Security validation failure: parent process has
    different executable'."""
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("_PYI") or key in ("_MEIPASS2", "_MEIPASS"):
            del env[key]
    return env


def _mint_login_ticket(base: str, token: str) -> str | None:
    """Ask the server for a one-time login ticket (over the authenticated header)
    so the master token never has to go in the window URL. Returns None if the
    server is older and doesn't offer /auth/ticket — the caller falls back."""
    try:
        request = urllib.request.Request(
            f"{base}/auth/ticket", method="POST", data=b"{}",
            headers={"X-GameGate-Token": token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read() or "null").get("ticket")
    except Exception:  # noqa: BLE001 — any failure just falls back to ?key=
        return None


def build_window_url(config: dict) -> str:
    """Dashboard URL for the desktop window. Prefer a one-time ?ticket= (minted
    over the authenticated header) so the master token stays out of the URL /
    webview history; fall back to ?key= against an older server."""
    base = config["api_url"].rstrip("/")
    token = config.get("api_token", "")
    if not token:
        return f"{base}/app"
    ticket = _mint_login_ticket(base, token)
    if ticket:
        return f"{base}/app?ticket={ticket}"
    return f"{base}/app?key={token}"


def open_window() -> None:
    """Launch the GameGate window as a separate process, so closing it never
    touches the tray/detector and the tray's single-instance lock stays clean."""
    if getattr(sys, "frozen", False):
        subprocess.Popen([sys.executable, "--window"], env=_child_env())
    else:
        subprocess.Popen([sys.executable, __file__, "--window"], env=_child_env())


def _darken_titlebar(window) -> None:
    """Native frame, dark trim: paint the Windows title bar in the app's own
    colors via DWM. Hardened after the first attempt silently failed on
    Jules' build: the form handle may not exist yet when the start callback
    fires, so retry, fall back to FindWindow by title, and re-apply once
    after the first paint."""
    try:
        import ctypes
        import time

        def get_hwnd() -> int:
            try:
                handle = window.native.Handle
                if hasattr(handle, "ToInt32"):
                    return int(handle.ToInt32())
                return int(handle)
            except Exception:  # noqa: BLE001 — fall through to FindWindow
                return int(ctypes.windll.user32.FindWindowW(None, "GameGate"))

        def apply(hwnd: int) -> None:
            dwm = ctypes.windll.dwmapi
            one = ctypes.c_int(1)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (19 pre-1903)
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(one), ctypes.sizeof(one))
            caption = ctypes.c_uint(0x00161110)  # sidebar color #101116 as 0x00BBGGRR
            text = ctypes.c_uint(0x00EFE8E6)     # text color #e6e8ef
            dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
            dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))

        hwnd = 0
        for _ in range(20):  # window/form may take a moment to materialize
            hwnd = get_hwnd()
            if hwnd:
                break
            time.sleep(0.25)
        if hwnd:
            apply(hwnd)
            time.sleep(0.6)   # some builds repaint the caption on first show
            apply(hwnd)
        else:
            log.warning("Dark titlebar: window handle never appeared")
    except Exception:
        log.exception("Dark titlebar not applied")


class UpdateChecker:
    """Dev-tier update detection: how many commits is origin/main ahead?
    (The public release-based tier is issue #72.)"""

    def __init__(self, repo_dir: Path, run_fn=None) -> None:
        self.repo_dir = repo_dir
        self.run = run_fn or self._run_git

    def _run_git(self, args: list[str]) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repo_dir), *args],
                capture_output=True, text=True, timeout=30, check=False,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def pending_changes(self) -> int:
        if self.run(["fetch", "--quiet"]) is None:
            return 0
        count = self.run(["rev-list", "HEAD..origin/main", "--count"])
        try:
            return int(count)
        except (TypeError, ValueError):
            return 0


def build_info() -> str:
    """'abc1234 · Aug 24 02:10' from build_info.json next to the exe/source,
    or 'source' / 'unstamped' when absent."""
    try:
        candidates = [app_dir() / "build_info.json"]
        if getattr(sys, "frozen", False):
            candidates.append(Path(sys.executable).parent / "build_info.json")
            # PyInstaller --add-data unpacks bundled files under _MEIPASS at
            # runtime; the release bundles build_info.json there so a downloaded
            # single-file exe still reports its real build, not "unstamped" (#13).
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.append(Path(meipass) / "build_info.json")
        for path in candidates:
            if path.exists():
                # utf-8-sig: PowerShell's Out-File -Encoding utf8 writes a BOM
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                return f"{data.get('build', '?')} · {data.get('built', '?')}"
        return "source" if not getattr(sys, "frozen", False) else "unstamped build"
    except Exception:  # noqa: BLE001 — cosmetic
        return "unknown"


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
    # Absolute path: on Jules' machine the bare word 'powershell' resolves
    # through a broken app association — the spawned console died instantly.
    powershell = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    launcher = str(powershell) if powershell.exists() else "powershell.exe"
    # Hidden console: the updater shows its own styled progress window.
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [launcher, "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
         "-File", str(script)],
        creationflags=creationflags, env=_child_env(),
    )
    return True


def _window_chrome(window) -> None:
    """Dark caption paint + Discord-style contextual title: the native bar's
    text follows the page (document.title), so the bar reads 'where you are'."""
    import time

    _darken_titlebar(window)
    while True:
        try:
            title = window.evaluate_js("document.title")
            if title and window.title != title:
                window.set_title(title)
        except Exception:  # noqa: BLE001 — window closed or JS not ready
            return
        time.sleep(1.5)


def run_window() -> None:
    """The desktop window: NATIVE frame (snap/maximize/Win+arrow all work)
    with the title bar painted in app colors — Edge WebView2 inside."""
    import webview

    config = load_config()
    window = webview.create_window(
        "GameGate", build_window_url(config),
        width=1080, height=760, background_color="#0e1011",
    )
    webview.start(_window_chrome, window)


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
                    icon.icon = icons.get(status.get("state"), icons["available"])
            except Exception:
                log.exception("Pump cycle failed")
            stop.wait(POLL_SECONDS)

    def on_status(icon, _item):
        status = api.get_status()
        state = status.get("state", "unknown") if status else "API unreachable"
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

    # Shared update state, filled by update_check_loop. None = not checked yet.
    update_status = {"count": None}

    def update_item_text(_item) -> str:
        count = update_status["count"]
        if count is None:
            return "Checking for updates…"
        if count > 0:
            return f"Update GameGate ({count})"
        return "Latest version"

    def update_item_enabled(_item) -> bool:
        # Greyed out unless a real update is waiting (review: 'Update' always shown).
        return bool(update_status["count"])

    def on_update(icon, _item):
        if not update_status["count"]:
            return  # nothing to update — the item is disabled anyway
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

    log.info("GameGate build: %s", build_info())
    icon = pystray.Icon(
        "GameGate", icons["available"], "GameGate",
        menu=pystray.Menu(
            pystray.MenuItem(f"Build: {build_info()}", None, enabled=False),
            pystray.MenuItem("Open GameGate", on_open, default=True),
            pystray.MenuItem("Status", on_status),
            pystray.MenuItem("Last digest", on_digest),
            pystray.MenuItem("Do Not Disturb", on_dnd),
            pystray.MenuItem(update_item_text, on_update, enabled=update_item_enabled),
            pystray.MenuItem("Quit", on_quit),
        ),
    )
    # Ctrl+C in the console stops everything cleanly (issue #33).
    def handle_sigint(_sig, _frame):
        stop.set()
        icon.stop()

    signal.signal(signal.SIGINT, handle_sigint)

    def update_check_loop():
        checker = UpdateChecker(update_script_path().parent.parent)
        stop.wait(20)  # let the app settle before the first check
        while not stop.is_set():
            try:
                count = checker.pending_changes()
                update_status["count"] = count       # drive the tray menu label/enabled
                with contextlib.suppress(Exception):
                    icon.update_menu()               # re-render 'Latest version' vs 'Update (N)'
                with contextlib.suppress(Exception):
                    api.report_update_status(count, build_info())  # so the dashboard shows it too
                if count > 0:
                    # Never interrupt a game with an update prompt — that's the
                    # exact thing GameGate exists to prevent (M22). Wait for a
                    # non-gaming moment.
                    status = api.get_status()
                    if status and status.get("state") == "gaming":
                        stop.wait(1800)  # re-check in 30 min
                        continue
                    wants_update = show_update_prompt(count)
                    if wants_update and launch_updater():
                        stop.set()
                        icon.stop()
                        return
                    stop.wait(4 * 3600)  # Later → snooze four hours
                    continue
            except Exception:
                log.exception("Update check failed")
            stop.wait(3600)  # check hourly

    threading.Thread(target=detector_loop, daemon=True).start()
    threading.Thread(target=pump_loop, args=(icon,), daemon=True).start()
    threading.Thread(target=update_check_loop, daemon=True).start()
    if config.get("capture_windows_notifications"):
        # Opt-in: mirror EVERY Windows notification into GameGate (Discord, Slack,
        # email, ...). Windows-only; the listener no-ops gracefully elsewhere.
        from windows_notifications import WindowsNotificationListener
        listener = WindowsNotificationListener(api.post_event)
        threading.Thread(target=listener.run, args=(stop,), daemon=True).start()
        log.info("Windows notification capture enabled")
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
