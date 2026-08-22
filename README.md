# GameGate

## Project goal

GameGate is an app that tracks when you play games — it looks at your system processes. From there, while playing, it'll keep log of Discord, Slack, Gmail and other important apps to withhold any non-urgent messages till you are done playing; urgent messages will notify you.

## Stack

- **Python 3.12** — primary language
- **FastAPI + Uvicorn** — the HTTP API layer
- **Pytest + httpx** — test suite
- **Ruff** — linting
- **SQLite** — persistence (planned, runbook Step 4)
- **GitHub Actions** — CI (planned, Step 12)
- **Nginx** — reverse proxy for deployment (planned, Step 11)

See [ARCHITECTURE.md](ARCHITECTURE.md) for how the pieces fit together.
