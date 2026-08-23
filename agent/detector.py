"""GameGate gaming detector.

Runs on the gaming PC. Watches for configured game processes and reports
STATE TRANSITIONS ONLY to the GameGate API — never a POST per poll cycle.
Survives API downtime: a failed report is retried on the next cycle because
last_reported_state only advances after a successful POST.

Run:  python detector.py            (uses agent/config.json)
      python detector.py --once     (single poll, for debugging)

Dependencies: psutil (pip install psutil). Stdlib otherwise, so the agent
stays a single copy-paste-able file on Windows.
"""
import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("gamegate.detector")

DEFAULT_CONFIG = {
    "api_url": "http://127.0.0.1:8000",
    "api_token": "",
    "game_processes": ["helldivers2.exe"],
    "poll_interval_seconds": 5,
}


def load_config(path: str | None = None) -> dict:
    config = dict(DEFAULT_CONFIG)
    config_path = Path(path) if path else Path(__file__).parent / "config.json"
    if config_path.exists():
        config.update(json.loads(config_path.read_text()))
    config["game_processes"] = [p.lower() for p in config["game_processes"]]
    return config


def psutil_process_lister() -> set[str]:
    import psutil

    names = set()
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"]:
                names.add(proc.info["name"].lower())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return names


class ApiClient:
    def __init__(self, base_url: str, token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def post_status(self, state: str, application: str | None, started_at: str | None) -> bool:
        body = json.dumps(
            {"state": state, "application": application, "started_at": started_at}
        ).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-GameGate-Token"] = self.token
        request = urllib.request.Request(
            f"{self.base_url}/status", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, TimeoutError) as exc:
            log.warning("GameGate API unreachable (%s) — will retry next cycle", exc)
            return False


class Detector:
    def __init__(self, config: dict, process_lister, api_client) -> None:
        self.config = config
        self.process_lister = process_lister
        self.api = api_client
        self.last_reported_state: str | None = None
        self.game_started_at: str | None = None

    def poll_once(self) -> None:
        running = self.process_lister()
        active_game = next(
            (p for p in self.config["game_processes"] if p in running), None
        )
        desired_state = "gaming" if active_game else "available"

        if desired_state == self.last_reported_state:
            return  # no transition, no traffic

        if desired_state == "gaming":
            started_at = datetime.now(UTC).isoformat()
            if self.api.post_status("gaming", active_game, started_at):
                self.last_reported_state = "gaming"
                self.game_started_at = started_at
                log.info("Transition -> GAMING (%s)", active_game)
        else:
            if self.api.post_status("available", None, None):
                self.last_reported_state = "available"
                self.game_started_at = None
                log.info("Transition -> AVAILABLE")

    def run_forever(self) -> None:
        log.info(
            "Detector watching %s every %ss",
            self.config["game_processes"],
            self.config["poll_interval_seconds"],
        )
        while True:
            try:
                self.poll_once()
            except Exception:
                log.exception("Poll failed; detector stays alive")
            time.sleep(self.config["poll_interval_seconds"])


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    detector = Detector(
        config,
        psutil_process_lister,
        ApiClient(config["api_url"], config["api_token"]),
    )
    if args.once:
        detector.poll_once()
    else:
        detector.run_forever()


if __name__ == "__main__":
    sys.exit(main())
