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
import json
import logging
import sys
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


def app_dir() -> Path:
    """Folder the app lives in. For a PyInstaller onefile build, __file__
    points into a temp extraction dir — config.json sits next to the .exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def load_config(path: str | None = None) -> dict:
    config = dict(DEFAULT_CONFIG)
    config_path = Path(path) if path else app_dir() / "config.json"
    if config_path.exists():
        config.update(json.loads(config_path.read_text()))
    config["game_processes"] = [p.lower() for p in config["game_processes"]]
    config["ignore_processes"] = [p.lower() for p in config.get("ignore_processes", [])]
    return config


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
    import re

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
        for suffix in ("client", "launcher", "-win64-shipping", "_win64", "win64", "x64", "shipping"):
            stripped = stem.removesuffix(suffix).rstrip("-_ ")
            if stripped != stem and stripped:
                stem, changed = stripped, True
    return (stem or name).title()


def steam_game_identity(exe_path: str) -> tuple[str, str] | None:
    """(friendly name, steam appid) from the library's appmanifest files.
    exe_path .../steamapps/common/<installdir>/... identifies the manifest."""
    import re
    from pathlib import Path as P

    match = re.search(r"(.*steamapps)[\\/]common[\\/]([^\\/]+)", exe_path, re.IGNORECASE)
    if not match:
        return None
    steamapps, installdir = P(match.group(1)), match.group(2).lower()
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
    import json as jsonlib
    import os as _os
    from pathlib import Path as P

    base = P(
        manifests_dir
        or _os.path.join(
            _os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
            "Epic", "EpicGamesLauncher", "Data", "Manifests",
        )
    )
    try:
        for item in base.glob("*.item"):
            data = jsonlib.loads(item.read_text(errors="ignore"))
            location = str(data.get("InstallLocation", "")).lower().rstrip("\\/")
            if location and exe_path.lower().startswith(location):
                return data.get("DisplayName") or None
    except OSError:
        return None
    return None


def gog_game_identity(exe_path: str) -> str | None:
    """Real title from GOG's goggame-*.info JSON sitting in the game folder."""
    import json as jsonlib
    from pathlib import Path as P

    folder = P(exe_path).parent
    try:
        for _ in range(3):  # exe may sit a couple of levels deep
            for info in folder.glob("goggame-*.info"):
                name = jsonlib.loads(info.read_text(errors="ignore")).get("name")
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


def windows_steam_running_app_id() -> int:
    """Steam writes the current game's app id here; 0 when not playing."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "RunningAppID")
            return int(value)
    except OSError:
        return 0


def detect_game(
    processes: dict[str, str],
    manual_list: list[str],
    auto_detect: bool = True,
    steam_app_id_reader=windows_steam_running_app_id,
    ignore_list: list[str] | None = None,
) -> str | None:
    """Return the detected game's label, or None. Layered:
    manual list → launcher-folder paths → Steam registry."""
    ignored = HELPER_PROCESSES | set(ignore_list or [])
    for name in manual_list:
        if name in processes:
            return name

    if not auto_detect:
        return None

    for name, exe_path in processes.items():
        if name in ignored:
            continue
        if any(marker in exe_path for marker in LIBRARY_MARKERS):
            return name

    app_id = steam_app_id_reader()
    if app_id:
        return f"steam-app-{app_id}"

    return None


class ApiClient:
    def __init__(self, base_url: str, token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def post_status(
        self, state: str, application: str | None, started_at: str | None,
        app_id: str | None = None,
    ) -> bool:
        body = json.dumps(
            {"state": state, "application": application, "started_at": started_at,
             "app_id": app_id}
        ).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-GameGate-Token"] = self.token
        request = urllib.request.Request(
            f"{self.base_url}/status", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return 200 <= response.status < 300
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

        if desired_state == self.last_reported_state:
            return  # no transition, no traffic

        if desired_state == "gaming":
            started_at = datetime.now(UTC).isoformat()
            display_name, app_id = resolve_display(active_game, processes)
            if self.api.post_status("gaming", display_name, started_at, app_id):
                self.last_reported_state = "gaming"
                self.game_started_at = started_at
                log.info("Transition -> GAMING (%s)", display_name)
        else:
            if self.api.post_status("available", None, None):
                self.last_reported_state = "available"
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
