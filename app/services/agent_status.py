"""Latest desktop-agent update status, reported by the tray app.

In-memory and advisory (single-user, single worker): the agent re-reports on
every update check (startup + hourly), so a server restart just means 'unknown'
until the next report. Lets the dashboard Settings area show the same
'Latest version' / 'Update available (N)' state the tray menu does.
"""
from datetime import UTC, datetime

_state: dict = {"pending": None, "build": None, "at": None}


def set_agent_update(pending: int, build: str) -> None:
    _state["pending"] = int(pending)
    _state["build"] = build or None
    _state["at"] = datetime.now(UTC).isoformat()


def get_agent_update() -> dict:
    return dict(_state)
