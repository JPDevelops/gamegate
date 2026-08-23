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
        self.digests = [{"id": "d1", "text": "Gaming session complete — 2h\n3 events"}]
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


def test_pump_shows_then_acks():
    api = FakeApi()
    shown = []
    pump = ToastPump(api, lambda title, body: shown.append((title, body)) or True)
    assert pump.run_once() == 2
    assert api.acked == ["n1", "d1"]
    assert "Urgent — GMAIL" in shown[0][0]
    assert pump.run_once() == 0  # drained — nothing re-shown


def test_pump_failed_toast_keeps_items_pending():
    api = FakeApi()
    pump = ToastPump(api, lambda title, body: False)
    assert pump.run_once() == 0
    assert api.acked == []
    assert len(api.pending_notifications()) == 1  # retried next cycle


def test_dnd_toggle_posts_focused_then_available():
    api = FakeApi()
    dnd = DndController(api)
    assert dnd.toggle() is True
    assert dnd.toggle() is False
    assert api.statuses == ["focused", "available"]


def test_dnd_stays_off_if_api_down():
    class DownApi(FakeApi):
        def post_status(self, *args):
            return False

    dnd = DndController(DownApi())
    assert dnd.toggle() is False  # no false confidence when the POST failed


def test_formatting():
    title, body = notification_title_body(
        {"source": "slack", "sender": "coworker", "title": "prod down"}
    )
    assert title == "Urgent — SLACK"
    assert "coworker" in body
    title, body = digest_title_body({"text": "Line1\nLine2\nLine3"})
    assert title == "Line1"
    assert "Line2" in body
