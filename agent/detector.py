"""GameGate gaming detector.

Runs on the gaming PC. Detects running games AUTOMATICALLY, three layers deep,
and reports STATE TRANSITIONS ONLY to the GameGate API:

1. Manual list — exact process names from config.json (always wins if set)
2. Launcher paths — any process whose executable lives inside a known game
   library folder (Steam, Epic, GOG, Riot, Xbox, Battle.net)
3. Steam registry — HKCU\\Software\\Valve\\Steam\\RunningAppID, which Steam
   sets to the running game's app id (0 when idle)

Survives API downtime: a failed report is retried on the next cycle because
last_reported_state only advances after a successful POST.

Run:  python detector.py            (uses agent/config.json if present)
      python detector.py --once     (single poll, for debugging)

Dependencies: psutil (pip install psutil). Stdlib otherwise.
"""
import argparse
import contextlib
import json
import logging
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("gamegate.detector")

DEFAULT_CONFIG = {
    "api_url": "http://127.0.0.1:8000",
    "api_token": "",
    "game_processes": [],          # optional manual additions
    "ignore_processes": [],        # never treat these as games (extends built-ins)
    "auto_detect": True,
    "poll_interval_seconds": 5,
    # Opt-in: capture ALL Windows notifications (Discord, Slack, email, ...) via
    # the OS notification listener and feed them into GameGate. Windows-only;
    # needs a one-time permission grant. Off by default; the first-run consent
    # prompt flips it on if the user says yes.
    "capture_windows_notifications": False,
    "windows_notif_prompted": False,   # have we shown the first-run consent ask?
    # Local mode: run the whole GameGate server inside this app on 127.0.0.1
    # instead of talking to a remote server. No server URL or token to enter —
    # api_url/api_token are filled in automatically at startup. On by default so
    # a packaged install "just works" offline; set false to point at a cloud
    # server via api_url/api_token instead.
    "local_mode": True,
}

# Path fragments that mark a process as "installed by a game launcher".
LIBRARY_MARKERS = (
    "\\steamapps\\common\\",
    "/steamapps/common/",
    "\\epic games\\",
    "\\gog galaxy\\games\\",
    "\\riot games\\",
    "\\xboxgames\\",
    "\\battle.net\\",
)

# Launcher/helper binaries that live in game folders but aren't the game —
# plus non-game apps commonly installed via Steam (Wallpaper Engine was a
# real false positive on Jules' PC, 2026-08-23: it lives in steamapps\common
# and runs 24/7, locking the state to GAMING forever).
HELPER_PROCESSES = {
    "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe",
    "epicwebhelper.exe", "crashpad_handler.exe", "gameoverlayui.exe",
    "easyanticheat.exe", "battle.net.exe", "riotclientservices.exe",
    "wallpaper32.exe", "wallpaper64.exe", "ui32.exe", "ui64.exe",
    "wallpaperservice32_c.exe", "webwallpaper32.exe", "webwallpaper64.exe",
}

# Built-in "this IS a game" process names for popular titles that DON'T live in a
# recognized launcher folder, so the path/Steam layers miss them (Jules played
# Minecraft 4h and it never registered, 2026-08-27). Maps the exe name -> a clean
# label. Only UNAMBIGUOUS names belong here. Minecraft Bedrock ships as
# Minecraft.Windows.exe under WindowsApps; Java Edition is handled separately (it
# runs as the generic javaw.exe, matched on its command line — see below).
KNOWN_GAMES = {
    "minecraft.windows.exe": "Minecraft",
}
_MINECRAFT_JAVA = "minecraft-java"  # sentinel for Java Edition (generic javaw.exe)


def app_dir() -> Path:
    """Folder the app lives in. For a PyInstaller onefile build, __file__
    points into a temp extraction dir — config.json sits next to the .exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _dir_writable(directory: Path) -> bool:
    try:
        probe = directory / ".gg_write_test"
        probe.write_text("x")
        probe.unlink()
        return True
    except OSError:
        return False


def config_path() -> Path:
    """Where config.json lives.

    - If a config.json already sits next to the app, use it. Covers the source
      checkout and the loose-exe dev build (update.ps1 drops config.json into
      dist/ beside GameGate.exe).
    - A packaged/frozen app otherwise uses a per-user LocalAppData folder. We do
      NOT probe the install dir for writability when frozen: under MSIX the
      install dir is read-only but writes are VIRTUALIZED, so the probe returns
      True and we'd silently read a redirected, empty config — falling back to
      the 127.0.0.1 default and never connecting to the real server.
    - A non-frozen source checkout with no beside-config uses its own dir if
      writable, else the per-user folder.

    Seeds the per-user copy from the bundled config.example.json on first use."""
    beside = app_dir() / "config.json"
    if beside.exists():
        return beside
    if not getattr(sys, "frozen", False) and _dir_writable(app_dir()):
        return beside
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GameGate"
    with contextlib.suppress(OSError):
        base.mkdir(parents=True, exist_ok=True)
    target = base / "config.json"
    if not target.exists():
        example = app_dir() / "config.example.json"
        if example.exists():
            with contextlib.suppress(OSError):
                target.write_text(example.read_text())
    return target


def data_dir() -> Path:
    """The writable per-user folder GameGate keeps its data in — the same folder
    config.json resolves to. In local mode the embedded server's SQLite database
    (gamegate.db) lives here too."""
    return config_path().parent


def load_config(path: str | None = None) -> dict:
    config = dict(DEFAULT_CONFIG)
    cfg = Path(path) if path else config_path()
    # Always log WHERE we read config from and whether it existed. When a
    # packaged app can't reach the server, the first question is "which config
    # file did it actually load?" — this answers it without guesswork.
    logging.getLogger("gamegate.detector").info(
        "Loading config from %s (exists=%s)", cfg, cfg.exists()
    )
    if cfg.exists():
        try:
            loaded = json.loads(cfg.read_text())
        except (ValueError, OSError) as exc:
            # A hand-edited config.json with a stray comma shouldn't brick the
            # whole tray app with a raw traceback — fall back to defaults and
            # log it so the user can fix the file (review: unguarded config load).
            logging.getLogger("gamegate.detector").warning(
                "Ignoring unreadable config at %s (%s); using defaults", cfg, exc
            )
            loaded = {}
        if isinstance(loaded, dict):
            config.update(loaded)
    config["game_processes"] = [p.lower() for p in config["game_processes"]]
    config["ignore_processes"] = [p.lower() for p in config.get("ignore_processes", [])]
    return config


def save_config_updates(updates: dict, path: str | None = None) -> bool:
    """Merge `updates` into config.json on disk and write it back atomically,
    preserving everything else the user has in the file. Used by the first-run
    consent flow so the app can flip a setting on itself instead of making the
    user hand-edit JSON. Returns False (and logs) rather than raising on error —
    a failed save must never crash startup."""
    cfg = Path(path) if path else config_path()
    log = logging.getLogger("gamegate.detector")
    try:
        current = {}
        if cfg.exists():
            with contextlib.suppress(ValueError, OSError):
                loaded = json.loads(cfg.read_text())
                if isinstance(loaded, dict):
                    current = loaded
        current.update(updates)
        fd, tmp = tempfile.mkstemp(
            dir=str(cfg.parent), prefix=".config.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(current, f, indent=2)
            os.replace(tmp, cfg)  # atomic; never a half-written config
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        return True
    except Exception:  # noqa: BLE001 — a bad save must not crash the app
        log.exception("Could not save config to %s", cfg)
        return False


def psutil_process_lister() -> dict[str, str]:
    """Running processes as {name_lower: exe_path_lower}."""
    import psutil

    processes: dict[str, str] = {}
    for proc in psutil.process_iter(["name", "exe"]):
        try:
            name = (proc.info["name"] or "").lower()
            if name:
                processes[name] = (proc.info["exe"] or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return processes


def parse_acf_fields(text: str) -> dict:
    """Minimal Valve ACF parser: top-level "key" "value" pairs we care about."""
    fields = {}
    for key in ("appid", "name", "installdir"):
        match = re.search(rf'"{key}"\s+"([^"]*)"', text)
        if match:
            fields[key] = match.group(1)
    return fields


def prettify_exe(name: str) -> str:
    """Fallback label when no Steam manifest matches: rustclient.exe -> Rust."""
    stem = name.rsplit(".", 1)[0].lower()
    changed = True
    while changed:
        changed = False
        suffixes = ("client", "launcher", "-win64-shipping", "_win64", "win64", "x64", "shipping")
        for suffix in suffixes:
            stripped = stem.removesuffix(suffix).rstrip("-_ ")
            if stripped != stem and stripped:
                stem, changed = stripped, True
    return (stem or name).title()


def steam_game_identity(exe_path: str) -> tuple[str, str] | None:
    """(friendly name, steam appid) from the library's appmanifest files.
    exe_path .../steamapps/common/<installdir>/... identifies the manifest."""
    match = re.search(r"(.*steamapps)[\\/]common[\\/]([^\\/]+)", exe_path, re.IGNORECASE)
    if not match:
        return None
    steamapps, installdir = Path(match.group(1)), match.group(2).lower()
    try:
        for manifest in steamapps.glob("appmanifest_*.acf"):
            fields = parse_acf_fields(manifest.read_text(errors="ignore"))
            if fields.get("installdir", "").lower() == installdir and fields.get("name"):
                return fields["name"], fields.get("appid", "")
    except OSError:
        return None
    return None


def epic_game_identity(exe_path: str, manifests_dir: str | None = None) -> str | None:
    """Real title from Epic's launcher manifests (*.item JSON files):
    match the exe path against each manifest's InstallLocation."""
    base = Path(
        manifests_dir
        or os.path.join(
            os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
            "Epic", "EpicGamesLauncher", "Data", "Manifests",
        )
    )
    try:
        for item in base.glob("*.item"):
            data = json.loads(item.read_text(errors="ignore"))
            location = str(data.get("InstallLocation", "")).lower().rstrip("\\/")
            if location and exe_path.lower().startswith(location):
                return data.get("DisplayName") or None
    except OSError:
        return None
    return None


def gog_game_identity(exe_path: str) -> str | None:
    """Real title from GOG's goggame-*.info JSON sitting in the game folder."""
    folder = Path(exe_path).parent
    try:
        for _ in range(3):  # exe may sit a couple of levels deep
            for info in folder.glob("goggame-*.info"):
                name = json.loads(info.read_text(errors="ignore")).get("name")
                if name:
                    return name
            folder = folder.parent
    except OSError:
        return None
    return None


def resolve_display(game: str, processes: dict[str, str]) -> tuple[str, str | None]:
    """Human name + optional Steam appid for the detected game label.
    Name resolution order: Steam manifest → Epic manifest → GOG info →
    prettified exe. Artwork id only exists for Steam titles."""
    if game in KNOWN_GAMES:
        return KNOWN_GAMES[game], None
    if game == _MINECRAFT_JAVA:
        return "Minecraft", None
    exe_path = processes.get(game, "")
    if exe_path:
        identity = steam_game_identity(exe_path)
        if identity:
            return identity[0], identity[1] or None
        for resolver in (epic_game_identity, gog_game_identity):
            name = resolver(exe_path)
            if name:
                return name, None
    if game.startswith("steam-app-"):
        return game, game.removeprefix("steam-app-")
    return prettify_exe(game), None


def windows_minecraft_java_running() -> bool:
    """True if Minecraft: Java Edition is running.

    Java Edition runs as the generic javaw.exe/java.exe — matching that by name
    would flag EVERY Java app (IDEs, servers, other Java games) as gaming, exactly
    the false-positive class that locked GAMING forever (Wallpaper Engine, N…). So
    we match the COMMAND LINE instead, which always references minecraft (the
    net.minecraft client main class, --gameDir, the versioned jar, the natives
    path). Precise and safe. Best-effort: any error or non-Windows -> False."""
    try:
        import psutil
    except ImportError:
        return False
    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name not in ("javaw.exe", "java.exe"):
                    continue
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if "minecraft" in cmdline:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:  # noqa: BLE001 — never let detection crash the poll
        return False
    return False


def windows_steam_running_app_id() -> int:
    """Steam writes the current game's app id here; 0 when not playing."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "RunningAppID")
            return int(value)
    except (OSError, ImportError, ValueError):
        # ImportError: winreg is Windows-only (non-Windows dev/CI). ValueError:
        # a non-int RunningAppID. Either way, "not playing" (N19).
        return 0


def detect_game(
    processes: dict[str, str],
    manual_list: list[str],
    auto_detect: bool = True,
    steam_app_id_reader=windows_steam_running_app_id,
    ignore_list: list[str] | None = None,
    minecraft_java_reader=windows_minecraft_java_running,
) -> str | None:
    """Return the detected game's label, or None. Layered:
    manual list → built-in known games → launcher-folder paths → Steam registry
    → Minecraft Java (command line)."""
    ignored = HELPER_PROCESSES | set(ignore_list or [])
    for name in manual_list:
        if name in processes:
            return name

    if not auto_detect:
        return None

    # Built-in known games (popular titles outside any launcher folder).
    for name in KNOWN_GAMES:
        if name in processes and name not in ignored:
            return name

    for name, exe_path in processes.items():
        if name in ignored:
            continue
        if any(marker in exe_path for marker in LIBRARY_MARKERS):
            return name

    app_id = steam_app_id_reader()
    if app_id:
        return f"steam-app-{app_id}"

    # Minecraft Java Edition: generic javaw.exe, identified by its command line.
    # Only pay for the cmdline scan when a Java process is actually running.
    if ("javaw.exe" in processes or "java.exe" in processes) and minecraft_java_reader():
        return _MINECRAFT_JAVA

    return None


class ApiClient:
    def __init__(self, base_url: str, token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def post_status(
        self, state: str, application: str | None, started_at: str | None,
        app_id: str | None = None,
    ) -> bool:
        return self._post_json("/status", {
            "state": state, "application": application,
            "started_at": started_at, "app_id": app_id,
        })

    def set_dnd(self, enabled: bool) -> bool:
        """Toggle the server-side DND override (POST /status/dnd) — the
        dashboard-authoritative endpoint, so tray and dashboard DND agree and the
        detector can't clobber it (review: two divergent DND mechanisms)."""
        return self._post_json("/status/dnd", {"enabled": enabled})

    def _post_json(self, path: str, payload: dict) -> bool:
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-GameGate-Token"] = self.token
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return 200 <= response.status < 300
        except urllib.error.HTTPError as exc:
            # HTTPError is a URLError subclass — catch it FIRST so an auth
            # failure isn't mislabeled "unreachable" (N34). This is the single
            # most likely setup mistake, so point at the real cause.
            if exc.code == 401:
                log.error(
                    "GameGate rejected the token (401) — check api_token in config.json"
                )
            else:
                log.warning("GameGate API error %s — will retry next cycle", exc.code)
            return False
        except (urllib.error.URLError, TimeoutError) as exc:
            log.warning("GameGate API unreachable (%s) — will retry next cycle", exc)
            return False


class Detector:
    def __init__(
        self,
        config: dict,
        process_lister,
        api_client,
        steam_app_id_reader=windows_steam_running_app_id,
    ) -> None:
        self.config = config
        self.process_lister = process_lister
        self.api = api_client
        self.steam_app_id_reader = steam_app_id_reader
        self.last_reported_state: str | None = None
        self.last_reported_game: str | None = None
        self.game_started_at: str | None = None

    def poll_once(self) -> None:
        processes = self.process_lister()
        active_game = detect_game(
            processes,
            self.config["game_processes"],
            self.config.get("auto_detect", True),
            self.steam_app_id_reader,
            self.config.get("ignore_processes"),
        )
        desired_state = "gaming" if active_game else "available"

        if desired_state == "gaming":
            display_name, app_id = resolve_display(active_game, processes)
            # Report on a game CHANGE too, not just available->gaming, so
            # switching titles mid-session opens a fresh session and recap
            # instead of staying attributed to the first game all night (M7).
            if (
                self.last_reported_state == "gaming"
                and display_name == self.last_reported_game
            ):
                return  # same game still running, no traffic
            started_at = datetime.now(UTC).isoformat()
            if self.api.post_status("gaming", display_name, started_at, app_id):
                self.last_reported_state = "gaming"
                self.last_reported_game = display_name
                self.game_started_at = started_at
                log.info("Transition -> GAMING (%s)", display_name)
        else:
            if desired_state == self.last_reported_state:
                return  # already available, no traffic
            if self.api.post_status("available", None, None):
                self.last_reported_state = "available"
                self.last_reported_game = None
                self.game_started_at = None
                log.info("Transition -> AVAILABLE")

    def run_forever(self) -> None:
        log.info(
            "Detector: auto_detect=%s, manual=%s, every %ss",
            self.config.get("auto_detect", True),
            self.config["game_processes"] or "(none)",
            self.config["poll_interval_seconds"],
        )
        while True:
            try:
                self.poll_once()
            except Exception:
                log.exception("Poll failed; detector stays alive")
            time.sleep(self.config["poll_interval_seconds"])


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    detector = Detector(
        config,
        psutil_process_lister,
        ApiClient(config["api_url"], config["api_token"]),
    )
    if args.once:
        detector.poll_once()
    else:
        detector.run_forever()


if __name__ == "__main__":
    sys.exit(main())
