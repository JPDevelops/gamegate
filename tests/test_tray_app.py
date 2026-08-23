"""Desktop app core logic — pump, DND, formatting — all OS-independent."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from tray_app import (
    DndController,
    ToastPump,
    digest_title_body,
    notification_title_body,
)


class FakeApi:
    def __init__(self):
        self.notifications = [
            {"id": "n1", "event": {"source": "gmail", "sender": "boss", "title": "Call me"}}
        ]
        self.digests = [{"id": "d1", "text": "Game Recap — g.exe, 2h 00m\n3 events"}]
        self.acked = []
        self.statuses = []

    def pending_notifications(self):
        return list(self.notifications)

    def ack_notification(self, nid):
        self.acked.append(nid)
        self.notifications = [n for n in self.notifications if n["id"] != nid]
        return True

    def pending_digests(self):
        return list(self.digests)

    def ack_digest(self, did):
        self.acked.append(did)
        self.digests = [d for d in self.digests if d["id"] != did]
        return True

    def post_status(self, state, application, started_at):
        self.statuses.append(state)
        return True

    def client_settings(self):
        return {"notification_sound": True, "overlay_duration_s": 8, "version": 1}


def test_pump_shows_then_acks():
    api = FakeApi()
    shown = []
    pump = ToastPump(api, lambda title, body, **kw: shown.append((title, body)) or True)
    assert pump.run_once() == 2
    assert api.acked == ["n1", "d1"]
    assert shown[0][0].endswith("— GMAIL")
    assert pump.run_once() == 0  # drained — nothing re-shown


def test_pump_failed_toast_keeps_items_pending():
    api = FakeApi()
    pump = ToastPump(api, lambda title, body, **kw: False)
    assert pump.run_once() == 0
    assert api.acked == []
    assert len(api.pending_notifications()) == 1  # retried next cycle


def test_dnd_toggle_posts_focused_then_available():
    api = FakeApi()
    dnd = DndController(api)
    assert dnd.toggle() is True
    assert dnd.toggle() is False
    assert api.statuses == ["focused", "available"]


def test_dnd_pauses_and_resyncs_detector():
    class FakeDetector:
        last_reported_state = "available"

    api = FakeApi()
    detector = FakeDetector()
    dnd = DndController(api, detector)
    assert dnd.toggle() is True          # DND on — detector loop will pause
    assert detector.last_reported_state == "available"  # untouched while on
    assert dnd.toggle() is False         # DND off
    assert detector.last_reported_state is None  # forced re-sync next poll


def test_dnd_stays_off_if_api_down():
    class DownApi(FakeApi):
        def post_status(self, *args):
            return False

    dnd = DndController(DownApi())
    assert dnd.toggle() is False  # no false confidence when the POST failed


def test_formatting():
    title, body = notification_title_body(
        {"source": "slack", "sender": "coworker", "title": "prod down", "priority": "urgent"}
    )
    assert title == "Urgent — SLACK"
    assert "coworker" in body
    title, _ = notification_title_body(
        {"source": "gmail", "sender": "amazon", "title": "shipped", "priority": "informational"}
    )
    assert title == "New — GMAIL"  # non-urgent cards must not cry wolf
    title, body = digest_title_body({"text": "Line1\nLine2\nLine3"})
    assert title == "Line1"
    assert "Line2" in body


def test_single_instance_lock_blocks_second_copy():
    from tray_app import acquire_single_instance_lock

    first = acquire_single_instance_lock()
    assert first is not None
    second = acquire_single_instance_lock()
    assert second is None  # a second GameGate refuses to start
    first.close()
    third = acquire_single_instance_lock()
    assert third is not None  # released cleanly after exit
    third.close()


def test_window_url_carries_login_key():
    from tray_app import build_window_url

    url = build_window_url({"api_url": "http://server/", "api_token": "tok123"})
    assert url == "http://server/app?key=tok123"
    assert build_window_url({"api_url": "http://server"}) == "http://server/app"



def test_pump_applies_settings_only_on_version_change():
    api = FakeApi()
    versions = [{"notification_sound": False, "overlay_duration_s": 12, "version": 5}]
    api.client_settings = lambda: versions[0]
    pump = ToastPump(api, lambda title, body, **kw: True)
    pump.run_once()
    assert pump.sound is False and pump.duration_s == 12
    # same version -> values stay applied, no churn
    pump.run_once()
    assert pump._settings_version == 5


def test_update_script_path_source_and_frozen(monkeypatch):
    import tray_app

    source_path = tray_app.update_script_path()
    assert source_path.name == "update.ps1"
    assert source_path.parent.name == "agent"

    monkeypatch.setattr(tray_app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(tray_app.sys, "executable", "/x/gamegate/agent/dist/GameGate.exe")
    frozen_path = tray_app.update_script_path()
    assert frozen_path.as_posix().endswith("agent/update.ps1")


def test_update_checker_counts_and_survives_git_failure(tmp_path):
    from tray_app import UpdateChecker

    calls = []

    def fake_git(args):
        calls.append(args)
        if args[0] == "fetch":
            return ""
        return "3"

    checker = UpdateChecker(tmp_path, run_fn=fake_git)
    assert checker.pending_changes() == 3
    assert calls[0][0] == "fetch"

    broken = UpdateChecker(tmp_path, run_fn=lambda args: None)
    assert broken.pending_changes() == 0

    garbage = UpdateChecker(tmp_path, run_fn=lambda args: "" if args[0] == "fetch" else "not-a-number")
    assert garbage.pending_changes() == 0


def test_build_info_reads_stamp(tmp_path, monkeypatch):
    import json as jsonlib

    import tray_app

    monkeypatch.setattr(tray_app, "app_dir", lambda: tmp_path)
    assert tray_app.build_info() == "source"
    (tmp_path / "build_info.json").write_text(jsonlib.dumps({"build": "abc1234", "built": "Aug 24 02:10"}))
    assert tray_app.build_info() == "abc1234 · Aug 24 02:10"
