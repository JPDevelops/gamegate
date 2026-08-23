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
