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
        self.abandoned = []
        self.statuses = []
        self.dnd_calls = []

    def pending_notifications(self):
        return list(self.notifications)

    def ack_notification(self, nid):
        self.acked.append(nid)
        self.notifications = [n for n in self.notifications if n["id"] != nid]
        return True

    def abandon_notification(self, nid):
        self.abandoned.append(nid)
        self.notifications = [n for n in self.notifications if n["id"] != nid]
        return True

    def pending_digests(self):
        return list(self.digests)

    def ack_digest(self, did):
        self.acked.append(did)
        self.digests = [d for d in self.digests if d["id"] != did]
        return True

    def abandon_digest(self, did):
        self.abandoned.append(did)
        self.digests = [d for d in self.digests if d["id"] != did]
        return True

    def post_status(self, state, application, started_at):
        self.statuses.append(state)
        return True

    def set_dnd(self, enabled):
        self.dnd_calls.append(enabled)
        return True

    def client_settings(self):
        return {"notification_sound": True, "overlay_duration_s": 8, "version": 1}


def test_pump_shows_then_acks():
    api = FakeApi()
    shown = []
    pump = ToastPump(api, lambda title, body, **kw: shown.append((title, body)) or True)
    assert pump.run_once() == 2
    assert api.acked == ["n1", "d1"]
    assert shown[0][0] == "boss"   # bold line = who it's from
    assert pump.run_once() == 0  # drained — nothing re-shown


def test_pump_failed_toast_keeps_items_pending():
    api = FakeApi()
    pump = ToastPump(api, lambda title, body, **kw: False)
    assert pump.run_once() == 0
    assert api.acked == []
    assert len(api.pending_notifications()) == 1  # retried next cycle


def test_pump_ack_failure_shows_once_then_gives_up():
    """B1: if display succeeds but ack keeps failing (e.g. a 401 after token
    rotation), the card is rendered ONCE and then given up — not re-shown every
    cycle forever. On the pre-fix code show is called every cycle."""
    api = FakeApi()
    api.digests = []
    api.ack_notification = lambda nid: False   # ack always fails
    shows = []
    pump = ToastPump(api, lambda title, body, **kw: shows.append(title) or True)
    for _ in range(6):
        pump.run_once()
    assert len(shows) == 1                      # rendered exactly once
    assert "n1" in pump._given_up               # retries exhausted → ignored


def test_giving_up_dead_letters_the_item_server_side(monkeypatch):
    """Review MAJOR: when the client exhausts its retries on a poison item, it
    tells the SERVER to abandon it, so the item leaves the pending queue and
    can't wedge newer items out of the oldest-200 window."""
    api = FakeApi()
    api.digests = []
    api.ack_notification = lambda nid: False   # n1 can never be acked
    pump = ToastPump(api, lambda title, body, **kw: True)
    for _ in range(6):
        pump.run_once()
    assert "n1" in api.abandoned                 # server was told to dead-letter it
    assert api.pending_notifications() == []     # it left the pending queue


def test_pump_display_failure_drops_after_max_attempts():
    """B1: a card that can't display gives up after MAX_SHOW_ATTEMPTS instead of
    being retried forever."""
    api = FakeApi()
    api.digests = []
    shows = []
    pump = ToastPump(api, lambda title, body, **kw: shows.append(title) or False)
    for _ in range(6):
        pump.run_once()
    assert len(shows) == pump.MAX_SHOW_ATTEMPTS  # tried MAX times, then stopped
    assert "n1" in pump._given_up


def test_dnd_toggle_uses_the_authoritative_dnd_endpoint():
    """The tray DND now drives the SAME server-side override the dashboard uses
    (POST /status/dnd), instead of posting focused/available as a base state —
    so the two DND surfaces agree (review: two divergent DND mechanisms)."""
    api = FakeApi()
    dnd = DndController(api)
    assert dnd.toggle() is True
    assert dnd.toggle() is False
    assert api.dnd_calls == [True, False]     # /status/dnd enabled then disabled
    assert api.statuses == []                 # no base-state focused/available posts


def test_dnd_stays_off_if_api_down():
    class DownApi(FakeApi):
        def set_dnd(self, enabled):
            return False

    dnd = DndController(DownApi())
    assert dnd.toggle() is False  # no false confidence when the POST failed


def test_formatting():
    # Bold line = WHO it's from; second line = the AI summary (what it is).
    # A captured text: who = the number/contact (title), what = the AI summary.
    title, body = notification_title_body({
        "source": "text", "sender": "Text", "title": "+19513964427",
        "content": "running late", "priority": "informational",
        "metadata": {"ai": {"summary": "A friend says they're running late."}},
    })
    assert title == "+19513964427"                           # who, not "New — TEXT"
    assert body == "A friend says they're running late."     # AI summary, not "Text: …"

    # Gmail: who = the sender; urgent keeps a marker without burying the sender.
    title, body = notification_title_body({
        "source": "gmail", "sender": "Boss <boss@work.com>", "title": "Deploy now",
        "content": "the site is down", "priority": "urgent",
    })
    assert title == "Urgent · Boss <boss@work.com>"
    assert body == "the site is down"                        # no AI summary → the text

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


def test_window_url_prefers_a_one_time_ticket(monkeypatch):
    """The master token must stay out of the window URL: when the server issues a
    one-time ticket, the URL carries ?ticket=; it falls back to ?key= only when
    the server is too old to mint one (review MAJOR: token-in-URL)."""
    import tray_app
    from tray_app import build_window_url

    # server mints a ticket → token never appears in the URL
    monkeypatch.setattr(tray_app, "_mint_login_ticket", lambda base, token: "TICKET1")
    url = build_window_url({"api_url": "http://server/", "api_token": "tok123"})
    assert url == "http://server/app?ticket=TICKET1"
    assert "tok123" not in url

    # older server (no ticket) → fall back to the key link
    monkeypatch.setattr(tray_app, "_mint_login_ticket", lambda base, token: None)
    assert build_window_url({"api_url": "http://server/", "api_token": "tok123"}) \
        == "http://server/app?key=tok123"

    # no token configured → plain URL
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


def test_child_env_scrubs_pyinstaller_vars(monkeypatch):
    import tray_app

    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "x")
    monkeypatch.setenv("_MEIPASS2", "y")
    monkeypatch.setenv("GAMEGATE_API_TOKEN", "keep-me")
    env = tray_app._child_env()
    assert "_PYI_ARCHIVE_FILE" not in env
    assert "_MEIPASS2" not in env
    assert env["GAMEGATE_API_TOKEN"] == "keep-me"
