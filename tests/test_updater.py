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
