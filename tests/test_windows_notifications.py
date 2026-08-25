"""Windows notification capture — the OS-independent mapping logic.

The winsdk plumbing is Windows-only and untestable here; these cover the pure
functions that decide source/priority and build the /events payload."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from windows_notifications import classify, map_notification_to_event


def test_classify_maps_known_apps_and_urgency():
    assert classify("Discord", "friend", "you up?")[0] == "discord"
    assert classify("Slack", "t", "b")[0] == "slack"
    assert classify("Mail", "t", "b")[0] == "gmail"
    assert classify("Some Random App", "t", "b")[0] == "system"  # unknown → system
    assert classify("Discord", "raid", "URGENT get on now")[1] == "urgent"
    assert classify("Discord", "hey", "lunch?")[1] == "informational"


def test_map_builds_a_valid_event_payload():
    ev = map_notification_to_event(
        "Discord", "teammate", "we need you", "abc123", "2026-08-24T00:00:00+00:00"
    )
    assert ev["source"] == "discord"
    assert ev["external_id"] == "win-abc123"      # stable → idempotent across polls
    assert ev["sender"] == "Discord"
    assert ev["title"] == "teammate" and ev["content"] == "we need you"
    assert ev["metadata"]["origin"] == "windows-notification"


def test_map_skips_gamegates_own_notifications():
    """Critical: never re-ingest our own overlay/toast, or the app loops forever."""
    assert map_notification_to_event(
        "GameGate", "Urgent — Call me", "boss", "self1", "2026-08-24T00:00:00+00:00"
    ) is None


def test_map_skips_empty_and_appless_notifications():
    assert map_notification_to_event("Discord", "", "", "e1", "2026-08-24T00:00:00+00:00") is None
    assert map_notification_to_event("", "t", "b", "e2", "2026-08-24T00:00:00+00:00") is None


def test_map_uses_friendly_name_and_strips_control_chars():
    """Real Discord case: the app id is an AUMID and the text carries invisible
    bidi isolates (U+2068 FSI / U+2069 PDI) that render as gibberish."""
    ev = map_notification_to_event(
        "com.squirrel.Discord.Discord",
        "Galactic (#general)",
        "⁨Galactic⁩: gg?",
        "42",
        "2026-08-24T00:00:00+00:00",
    )
    assert ev["source"] == "discord"          # still matched from the raw AUMID
    assert ev["sender"] == "Discord"          # but shown clean
    assert ev["content"] == "Galactic: gg?"   # bidi isolates stripped
    assert "⁨" not in ev["content"] and "⁩" not in ev["content"]
