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
