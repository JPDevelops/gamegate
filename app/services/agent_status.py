"""Latest desktop-agent update status, reported by the tray app.

In-memory and advisory (single-user, single worker): the agent re-reports on
every update check (startup + hourly), so a server restart just means 'unknown'
until the next report. Lets the dashboard Settings area show the same
'Latest version' / 'Update available (N)' state the tray menu does.
"""
from datetime import UTC, datetime

_state: dict = {
    "pending": None, "build": None, "version": None,
    "available_version": None, "at": None,
}
# Set by the dashboard when the user clicks "Update now"; the tray polls + clears
# it and then applies the update. In-memory (single-user, single worker).
_apply_requested: dict = {"flag": False}


def set_agent_update(
    pending: int, build: str, version: str = "", available_version: str = ""
) -> None:
    _state["pending"] = int(pending)
    _state["build"] = build or None
    _state["version"] = version or None
    _state["available_version"] = available_version or None
    _state["at"] = datetime.now(UTC).isoformat()


def get_agent_update() -> dict:
    return dict(_state)


def request_apply() -> None:
    """Dashboard → 'Update now': ask the tray to apply the pending update."""
    _apply_requested["flag"] = True


def take_apply_request() -> bool:
    """Tray polls this: returns whether an apply was requested, and consumes it."""
    was = _apply_requested["flag"]
    _apply_requested["flag"] = False
    return was
