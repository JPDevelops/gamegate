"""Version-comparison logic for the in-app auto-updater (network-free)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import updater


def test_parse_version_and_ordering():
    assert updater.parse_version("v0.3.10") == (0, 3, 10)
    assert updater.parse_version("0.3.1") == (0, 3, 1)
    assert updater.parse_version("v1.0.0-beta") == (1, 0, 0)
    assert updater.parse_version("") == (0,)
    # Numeric ordering, not string ordering (0.3.10 > 0.3.9).
    assert updater.parse_version("v0.3.10") > updater.parse_version("v0.3.9")
    assert updater.parse_version("v0.4.0") > updater.parse_version("v0.3.99")
    assert updater.parse_version("v0.3.1") <= updater.parse_version("v0.3.1")


def test_check_and_update_noop_when_not_frozen(monkeypatch):
    """A source/dev run must never try to self-update (the git updater owns that)."""
    monkeypatch.setattr(updater.sys, "frozen", False, raising=False)
    called = []
    monkeypatch.setattr(updater, "latest_release", lambda: called.append(1) or ("v9.9.9", "http://x"))
    assert updater.check_and_update("0.3.1") is False
    assert called == []  # returned before even checking


def test_update_backs_off_after_recent_attempt(monkeypatch, tmp_path):
    """Loop-proofing: if a swap didn't stick (still old after a recent attempt on
    the SAME tag), the next check backs off instead of re-applying — no restart
    loop, even when a release's asset is stale/mismatched."""
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater, "_marker_path", lambda: tmp_path / "mark.json")
    monkeypatch.setattr(updater, "latest_release", lambda: ("v9.9.9", "http://x/GameGate.exe"))
    swaps = []
    monkeypatch.setattr(updater, "_download_and_launch_swap", lambda url: swaps.append(url) or True)

    assert updater.check_and_update("0.3.5") is True    # first time: attempts
    assert len(swaps) == 1
    assert updater.check_and_update("0.3.5") is False   # still old -> backs off
    assert len(swaps) == 1                              # did NOT loop


def test_update_clears_marker_once_current(monkeypatch, tmp_path):
    mp = tmp_path / "mark.json"
    mp.write_text('{"tag": "v9.9.9", "at": 9999999999}')
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater, "_marker_path", lambda: mp)
    monkeypatch.setattr(updater, "latest_release", lambda: ("v0.3.5", "http://x/GameGate.exe"))
    assert updater.check_and_update("0.3.5") is False   # up to date
    assert not mp.exists()                              # stale marker cleared


def test_available_update_checks_without_applying(monkeypatch, tmp_path):
    """available_update returns the (tag, url) to OFFER but never downloads or
    records an attempt — so a user who's only being *asked* isn't marked as
    having tried (that would wrongly suppress the re-offer)."""
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater, "_marker_path", lambda: tmp_path / "mark.json")
    monkeypatch.setattr(updater, "latest_release", lambda: ("v9.9.9", "http://x/GameGate.exe"))
    swaps = []
    monkeypatch.setattr(updater, "_download_and_launch_swap", lambda url: swaps.append(url) or True)

    info = updater.available_update("0.3.5")
    assert info == ("v9.9.9", "http://x/GameGate.exe")
    assert swaps == []                                   # nothing downloaded/applied
    assert not (tmp_path / "mark.json").exists()         # no attempt recorded on a mere check

    # Up to date → None.
    monkeypatch.setattr(updater, "latest_release", lambda: ("v0.3.5", "http://x/GameGate.exe"))
    assert updater.available_update("0.3.5") is None


def test_apply_update_records_then_swaps(monkeypatch, tmp_path):
    mp = tmp_path / "mark.json"
    monkeypatch.setattr(updater, "_marker_path", lambda: mp)
    swaps = []
    monkeypatch.setattr(updater, "_download_and_launch_swap", lambda url: swaps.append(url) or True)
    assert updater.apply_update("v9.9.9", "http://x/GameGate.exe") is True
    assert swaps == ["http://x/GameGate.exe"]
    assert mp.exists()                                   # attempt recorded before swap


def _stub_swap_env(monkeypatch, tmp_path, new_bytes=b"NEW-BUILD" * 200_000):
    """A fake 'freshly-downloaded exe' at sys.executable + an installed target."""
    src = tmp_path / "GameGate-new.exe"
    src.write_bytes(new_bytes)
    target = tmp_path / "GameGate.exe"
    target.write_bytes(b"OLD-BUILD")
    monkeypatch.setattr(updater.sys, "executable", str(src))
    monkeypatch.setattr(updater.sys, "argv", ["gg", "--apply-update", str(target)])
    monkeypatch.setattr(updater.time, "sleep", lambda *_: None)
    monkeypatch.setattr(updater, "_kill_processes_using", lambda *_: None)
    launched = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))
    return src, target, launched


def test_apply_update_mode_swaps_atomically(monkeypatch, tmp_path):
    """The installed exe ends up as the FULL new build and the app is relaunched;
    no stray sidecar file is left next to it."""
    src, target, launched = _stub_swap_env(monkeypatch, tmp_path)
    updater.apply_update_mode()
    assert target.read_bytes() == src.read_bytes()          # fully replaced
    assert launched == [[str(target), "--show"]]            # relaunched with the window
    # No half-written sidecar litter left behind.
    assert [p.name for p in tmp_path.iterdir() if ".new-" in p.name] == []


def test_apply_update_mode_leaves_old_exe_intact_if_replace_fails(monkeypatch, tmp_path):
    """If the atomic replace never succeeds (target stays locked), the installed
    exe must remain the OLD working build — never a truncated file — and nothing
    is relaunched. This is the crash-loop guard: a failed swap can't brick the app."""
    src, target, launched = _stub_swap_env(monkeypatch, tmp_path)
    monkeypatch.setattr(updater.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("locked")))
    updater.apply_update_mode()
    assert target.read_bytes() == b"OLD-BUILD"              # untouched, not truncated
    assert launched == []                                   # did not relaunch a bad exe
    assert [p.name for p in tmp_path.iterdir() if ".new-" in p.name] == []  # sidecar cleaned up
