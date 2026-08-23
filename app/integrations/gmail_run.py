"""Gmail polling loop. Run: python -m app.integrations.gmail_run

Requires GMAIL_ENABLED=true and OAuth files (docs/GMAIL_SETUP.md). Polls on an
interval, ingests through the authenticated API; duplicates are absorbed by
(source, external_id) idempotency, and a Gmail outage just skips a cycle.
"""
import logging
import os
import time

from app.integrations.discord_connector import GameGateApi
from app.integrations.gmail_connector import GmailPoller, build_real_client

log = logging.getLogger("gamegate.gmail.run")

POLL_SECONDS = int(os.environ.get("GMAIL_POLL_SECONDS", "120"))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if os.environ.get("GMAIL_ENABLED", "").lower() != "true":
        log.info("Gmail connector disabled (GMAIL_ENABLED != true)")
        return
    api = GameGateApi(
        os.environ.get("GAMEGATE_API_URL", "http://127.0.0.1:8000"),
        os.environ.get("GAMEGATE_API_TOKEN", ""),
    )
    poller = GmailPoller(build_real_client(), api)
    log.info("Gmail poller running every %ss", POLL_SECONDS)
    while True:
        ingested = poller.poll_once()
        if ingested:
            log.info("Ingested %s message(s)", ingested)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
