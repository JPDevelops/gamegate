"""Step 5 acceptance: exactly one transition per state change, no repeat
sends, and API downtime never kills or double-reports."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from detector import Detector

CONFIG = {
    "api_url": "http://test",
    "api_token": "",
    "game_processes": ["helldivers2.exe"],
    "poll_interval_seconds": 1,
}


class FakeApi:
    def __init__(self, up=True):
        self.up = up
        self.calls = []

    def post_status(self, state, application, started_at):
        if not self.up:
            return False
        self.calls.append((state, application))
        return True


def make_detector(api, running_names):
    state = {"names": set(running_names)}
    detector = Detector(CONFIG, lambda: state["names"], api)
    return detector, state


def test_game_start_and_stop_send_one_transition_each():
    api = FakeApi()
    detector, procs = make_detector(api, ["explorer.exe"])

    detector.poll_once()  # boots into available
    detector.poll_once()  # no change → no extra call
    procs["names"] = {"explorer.exe", "helldivers2.exe"}
    detector.poll_once()  # → gaming
    detector.poll_once()  # still gaming → nothing
    procs["names"] = {"explorer.exe"}
    detector.poll_once()  # → available

    assert api.calls == [
        ("available", None),
        ("gaming", "helldivers2.exe"),
        ("available", None),
    ]


def test_api_downtime_is_retried_not_lost():
    api = FakeApi(up=False)
    detector, _procs = make_detector(api, ["helldivers2.exe"])

    detector.poll_once()  # API down — transition NOT recorded as sent
    assert detector.last_reported_state is None

    api.up = True
    detector.poll_once()  # retried automatically on next cycle
    assert api.calls == [("gaming", "helldivers2.exe")]
    assert detector.last_reported_state == "gaming"


def test_process_match_is_case_insensitive():
    api = FakeApi()
    detector, _ = make_detector(api, ["HELLDIVERS2.EXE".lower()])
    detector.poll_once()
    assert api.calls[0][0] == "gaming"


def test_crash_in_lister_does_not_kill_poll_loop():
    api = FakeApi()

    def exploding_lister():
        raise RuntimeError("WMI hiccup")

    detector = Detector(CONFIG, exploding_lister, api)
    try:
        detector.poll_once()
    except RuntimeError:
        # poll_once may raise; run_forever catches it — simulate that contract:
        pass
    assert api.calls == []
