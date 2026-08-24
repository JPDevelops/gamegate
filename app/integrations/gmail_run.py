"""Gmail polling loop. Run: python -m app.integrations.gmail_run

Requires GMAIL_ENABLED=true and OAuth files (docs/GMAIL_SETUP.md). Polls on an
interval, ingests through the authenticated API; duplicates are absorbed by
(source, external_id) idempotency, and a Gmail outage just skips a cycle.
"""
import logging
import os
import time

from app.api.connectors import connector_enabled
from app.integrations.discord_connector import GameGateApi
from app.integrations.gmail_connector import GmailPoller, build_real_client

log = logging.getLogger("gamegate.gmail.run")

POLL_SECONDS = int(os.environ.get("GMAIL_POLL_SECONDS", "120"))


def main() -> None:
    """Runs continuously under systemd and self-gates on the GMAIL_ENABLED flag
    (which the dashboard's connect/disconnect toggles) — so the web API never
    needs sudo to start/stop this service (review B2). When disabled it idles;
    when enabled it (lazily) builds the client and polls."""
    logging.basicConfig(level=logging.INFO)
    api = GameGateApi(
        os.environ.get("GAMEGATE_API_URL", "http://127.0.0.1:8000"),
        os.environ.get("GAMEGATE_API_TOKEN", ""),
    )
    poller: GmailPoller | None = None
    log.info("Gmail runner up; polling every %ss when enabled", POLL_SECONDS)
    while True:
        if connector_enabled("gmail"):
            try:
                if poller is None:
                    poller = GmailPoller(build_real_client(), api)  # lazy: only when on
                ingested = poller.poll_once()
                if ingested:
                    log.info("Ingested %s message(s)", ingested)
                api.heartbeat("gmail", True)   # a clean cycle → connector is healthy
            except Exception as exc:
                log.exception("Gmail poll failed; will retry next cycle")
                api.heartbeat("gmail", False, str(exc))  # surface it on the dashboard
                poller = None  # rebuild the client next time (e.g. after re-auth)
        else:
            poller = None  # drop the client while disabled
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
