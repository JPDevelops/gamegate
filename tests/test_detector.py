"""Step 5 acceptance + auto-detection: one transition per state change,
retry on API downtime, and games found WITHOUT manual configuration."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from detector import Detector, detect_game

CONFIG = {
    "api_url": "http://test",
    "api_token": "",
    "game_processes": [],
    "auto_detect": True,
    "poll_interval_seconds": 1,
}

DESKTOP = {
    "explorer.exe": r"c:\windows\explorer.exe",
    "discord.exe": r"c:\users\jules\appdata\local\discord\discord.exe",
    "steam.exe": r"c:\program files (x86)\steam\steam.exe",
}
STEAM_GAME = {
    "helldivers2.exe": r"c:\program files (x86)\steam\steamapps\common\helldivers 2\bin\helldivers2.exe"
}
EPIC_GAME = {
    "fortniteclient-win64-shipping.exe": r"c:\program files\epic games\fortnite\fortniteclient-win64-shipping.exe"
}


class FakeApi:
    def __init__(self, up=True):
        self.up = up
        self.calls = []

    def post_status(self, state, application, started_at, app_id=None):
        if not self.up:
            return False
        self.calls.append((state, application))
        return True


def no_steam():
    return 0


# --- detect_game: the three layers ---------------------------------------

def test_steam_library_path_is_detected_without_config():
    assert detect_game({**DESKTOP, **STEAM_GAME}, [], True, no_steam) == "helldivers2.exe"


def test_epic_library_path_is_detected_without_config():
    game = detect_game({**DESKTOP, **EPIC_GAME}, [], True, no_steam)
    assert game == "fortniteclient-win64-shipping.exe"


def test_helper_processes_in_game_folders_are_not_games():
    processes = {
        **DESKTOP,
        "gameoverlayui.exe": r"c:\program files (x86)\steam\steamapps\common\x\gameoverlayui.exe",
    }
    assert detect_game(processes, [], True, no_steam) is None


def test_steam_registry_catches_games_outside_library_paths():
    assert detect_game(DESKTOP, [], True, lambda: 553850) == "steam-app-553850"


def test_manual_list_still_wins():
    processes = {**DESKTOP, "myindiegame.exe": r"d:\games\myindiegame.exe"}
    assert detect_game(processes, ["myindiegame.exe"], True, no_steam) == "myindiegame.exe"


def test_auto_detect_off_uses_manual_only():
    assert detect_game({**DESKTOP, **STEAM_GAME}, [], False, no_steam) is None


def test_plain_desktop_is_not_gaming():
    assert detect_game(DESKTOP, [], True, no_steam) is None


def test_malformed_config_falls_back_to_defaults(tmp_path):
    """A hand-edited config.json with bad JSON must not brick startup with a raw
    traceback — load_config logs and returns defaults (review: unguarded load)."""
    from detector import load_config

    bad = tmp_path / "config.json"
    bad.write_text('{"game_processes": ["x.exe",]}')  # trailing comma → invalid JSON
    config = load_config(str(bad))
    assert isinstance(config, dict)
    assert "game_processes" in config and isinstance(config["game_processes"], list)


# --- Detector transitions --------------------------------------------------

def make_detector(api, initial_processes):
    state = {"procs": dict(initial_processes), "steam": 0}
    detector = Detector(
        CONFIG, lambda: state["procs"], api, steam_app_id_reader=lambda: state["steam"]
    )
    return detector, state


def test_game_start_and_stop_send_one_transition_each():
    api = FakeApi()
    detector, state = make_detector(api, DESKTOP)

    detector.poll_once()  # boots into available
    detector.poll_once()  # no change → no extra call
    state["procs"] = {**DESKTOP, **STEAM_GAME}
    detector.poll_once()  # → gaming (auto-detected, no config)
    detector.poll_once()  # still gaming → nothing
    state["procs"] = dict(DESKTOP)
    detector.poll_once()  # → available

    assert api.calls == [
        ("available", None),
        ("gaming", "Helldivers2"),  # display name resolved from the exe
        ("available", None),
    ]


def test_api_downtime_is_retried_not_lost():
    api = FakeApi(up=False)
    detector, _state = make_detector(api, {**DESKTOP, **STEAM_GAME})

    detector.poll_once()  # API down — transition NOT recorded as sent
    assert detector.last_reported_state is None

    api.up = True
    detector.poll_once()  # retried automatically on next cycle
    assert api.calls == [("gaming", "Helldivers2")]
    assert detector.last_reported_state == "gaming"


def test_steam_registry_transition():
    api = FakeApi()
    detector, state = make_detector(api, DESKTOP)
    detector.poll_once()
    state["steam"] = 553850
    detector.poll_once()
    assert api.calls[-1] == ("gaming", "steam-app-553850")


def test_wallpaper_engine_is_not_a_game():
    """Real false positive from Jules' PC (2026-08-23): Wallpaper Engine
    lives in steamapps\\common and runs constantly."""
    processes = {
        **DESKTOP,
        "wallpaper64.exe": r"c:\program files (x86)\steam\steamapps\common\wallpaper_engine\wallpaper64.exe",
    }
    assert detect_game(processes, [], True, no_steam) is None


def test_config_ignore_list_extends_builtins():
    processes = {
        **DESKTOP,
        "notagame.exe": r"c:\program files (x86)\steam\steamapps\common\tool\notagame.exe",
    }
    assert detect_game(processes, [], True, no_steam) == "notagame.exe"
    assert detect_game(processes, [], True, no_steam, ["notagame.exe"]) is None


def test_frozen_build_reads_config_next_to_exe(tmp_path, monkeypatch):
    """PyInstaller onefile: __file__ is a temp dir; config.json lives next to
    the .exe (sys.executable)."""
    import json as jsonlib

    import detector as detector_module

    exe = tmp_path / "GameGate.exe"
    exe.write_bytes(b"")
    (tmp_path / "config.json").write_text(jsonlib.dumps({"api_url": "http://from-exe-dir"}))
    monkeypatch.setattr(detector_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(detector_module.sys, "executable", str(exe))
    config = detector_module.load_config()
    assert config["api_url"] == "http://from-exe-dir"


ACF = '''
"AppState"
{
\t"appid"\t\t"252490"
\t"name"\t\t"Rust"
\t"installdir"\t\t"Rust"
}
'''


def test_acf_parsing_and_identity(tmp_path):
    from detector import parse_acf_fields, resolve_display, steam_game_identity

    fields = parse_acf_fields(ACF)
    assert fields == {"appid": "252490", "name": "Rust", "installdir": "Rust"}

    steamapps = tmp_path / "steamapps"
    (steamapps / "common" / "Rust").mkdir(parents=True)
    (steamapps / "appmanifest_252490.acf").write_text(ACF)
    exe = str(steamapps / "common" / "Rust" / "rustclient.exe")
    assert steam_game_identity(exe) == ("Rust", "252490")

    name, app_id = resolve_display("rustclient.exe", {"rustclient.exe": exe})
    assert (name, app_id) == ("Rust", "252490")


def test_prettify_fallback_for_non_steam_games():
    from detector import prettify_exe, resolve_display

    assert prettify_exe("rustclient.exe") == "Rust"
    assert prettify_exe("fortniteclient-win64-shipping.exe") == "Fortnite"
    name, app_id = resolve_display("myindiegame.exe", {"myindiegame.exe": r"d:\games\myindiegame.exe"})
    assert name == "Myindiegame" and app_id is None


def test_epic_identity_from_item_manifest(tmp_path):
    import json as jsonlib

    from detector import epic_game_identity

    install = tmp_path / "Epic Games" / "Fortnite"
    install.mkdir(parents=True)
    manifests = tmp_path / "Manifests"
    manifests.mkdir()
    (manifests / "abc.item").write_text(jsonlib.dumps(
        {"DisplayName": "Fortnite", "InstallLocation": str(install)}
    ))
    exe = str(install / "FortniteClient-Win64-Shipping.exe")
    assert epic_game_identity(exe, str(manifests)) == "Fortnite"
    assert epic_game_identity(str(tmp_path / "elsewhere" / "x.exe"), str(manifests)) is None


def test_gog_identity_from_info_file(tmp_path):
    import json as jsonlib

    from detector import gog_game_identity

    game_dir = tmp_path / "GOG" / "Cyberpunk 2077" / "bin" / "x64"
    game_dir.mkdir(parents=True)
    (tmp_path / "GOG" / "Cyberpunk 2077" / "goggame-1423049311.info").write_text(
        jsonlib.dumps({"name": "Cyberpunk 2077"})
    )
    assert gog_game_identity(str(game_dir / "Cyberpunk2077.exe")) == "Cyberpunk 2077"


def test_save_config_updates_merges_and_preserves(tmp_path):
    """The first-run consent flow writes settings back without clobbering the
    user's existing config (review: app can flip its own setting on)."""
    import json

    from detector import save_config_updates

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"api_url": "https://x", "api_token": "keep-me"}))
    assert save_config_updates(
        {"capture_windows_notifications": True, "windows_notif_prompted": True},
        str(cfg),
    )
    data = json.loads(cfg.read_text())
    assert data["api_token"] == "keep-me"          # existing values preserved
    assert data["api_url"] == "https://x"
    assert data["capture_windows_notifications"] is True
    assert data["windows_notif_prompted"] is True
    assert list(tmp_path.glob(".config.*.tmp")) == []   # atomic write cleaned up


def test_config_path_falls_back_to_localappdata_when_install_is_readonly(tmp_path, monkeypatch):
    """A packaged (MSIX) app runs from a read-only dir, so config must live in a
    writable per-user LocalAppData folder instead (review: packaging)."""
    import detector as d

    monkeypatch.setattr(d, "_dir_writable", lambda _p: False)  # simulate read-only install
    monkeypatch.setattr(d, "app_dir", lambda: tmp_path / "install")   # no config beside it
    (tmp_path / "install").mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    p = d.config_path()
    assert p == tmp_path / "AppData" / "GameGate" / "config.json"
    assert p.parent.is_dir()  # created


def test_config_path_ignores_write_probe_when_frozen(tmp_path, monkeypatch):
    """The MSIX failure that shipped a 127.0.0.1 window: under MSIX the install
    dir is read-only but writes are VIRTUALIZED, so the write-probe returns True
    and the app read a redirected, empty config. A FROZEN app must ignore the
    probe and use LocalAppData whenever no real config sits beside the exe.

    Fails on the pre-fix code (probe==True -> returns the beside path)."""
    import detector as d

    install = tmp_path / "install"       # no config.json beside the exe
    install.mkdir()
    monkeypatch.setattr(d.sys, "frozen", True, raising=False)
    monkeypatch.setattr(d, "app_dir", lambda: install)
    monkeypatch.setattr(d, "_dir_writable", lambda _p: True)   # virtualization lies
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))

    p = d.config_path()
    assert p == tmp_path / "AppData" / "GameGate" / "config.json"
    assert p != install / "config.json"


def test_config_path_prefers_real_config_beside_loose_exe(tmp_path, monkeypatch):
    """The loose-exe dev build (update.ps1 drops config.json into dist/) must
    keep reading that beside-config even though it is frozen too."""
    import detector as d

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "config.json").write_text("{}")
    monkeypatch.setattr(d.sys, "frozen", True, raising=False)
    monkeypatch.setattr(d, "app_dir", lambda: dist)
    assert d.config_path() == dist / "config.json"
