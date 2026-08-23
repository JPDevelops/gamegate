"""Slack Socket Mode runner. Run: python -m app.integrations.slack_run

Requires SLACK_ENABLED=true, SLACK_BOT_TOKEN (xoxb-) and SLACK_APP_TOKEN
(xapp-). See docs/SLACK_SETUP.md.
"""
import logging
import os

from app.integrations.discord_connector import GameGateApi
from app.integrations.slack_connector import run_socket_mode


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    api = GameGateApi(
        os.environ.get("GAMEGATE_API_URL", "http://127.0.0.1:8000"),
        os.environ.get("GAMEGATE_API_TOKEN", ""),
    )
    run_socket_mode(api)


if __name__ == "__main__":
    main()
