# GameGate

## Project goal

GameGate is an app that tracks when you play games — it looks at your system processes. From there, while playing, it'll keep log of Discord, Slack, Gmail and other important apps to withhold any non-urgent messages till you are done playing; urgent messages will notify you. When the session ends, you get one digest of everything you missed.

## How it works

```
Gaming PC                      Server                          External
detector.py  ──POST /status──> Nginx → FastAPI ←──poll──  Gmail (read-only)
(psutil, transitions only)       │        ↑ ←──socket──  Slack (mentions)
                                 │        ↑ ←──gateway─  Discord (messages)
                              SQLite      │
                                 │     routing engine → deliver now / queue
                                 └──── session ends → ONE digest → Discord
```

Every external message becomes one internal **Event** `(source, external_id, sender, title, priority, …)`, stored idempotently — the same message can never be ingested twice. The **routing engine** combines your current state (available / focused / gaming / away) with the event's priority to deliver immediately, queue for the digest, or suppress. An optional **AI classifier** enriches events but always falls back to deterministic rules.

## Stack

- **Python 3.12**, **FastAPI + Uvicorn** — API layer
- **SQLite** — persistence behind a repository layer
- **Pytest + httpx** — 74+ tests, all offline (fakes/mocks for every external service)
- **Ruff** — linting; **GitHub Actions** — CI on every push/PR
- **Nginx + systemd** — production-style deployment (`nginx/`, `deploy/`)

## Prerequisites

- Python 3.12+
- (Gaming PC only) `pip install psutil`
- Optional integrations: `pip install -e ".[discord]"`, `".[gmail]"`, `".[slack]"`

## Run it locally

```bash
git clone https://github.com/JPDevelops/gamegate.git && cd gamegate
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e . && pip install pytest httpx ruff
cp .env.example .env                                  # fill in values
uvicorn app.main:app --reload                         # http://127.0.0.1:8000/docs
```

## Run the tests

```bash
pytest          # entire suite, no network needed
ruff check app tests agent
```

## Configuration

All configuration is environment variables — see [`.env.example`](.env.example). Key ones:

| Variable | Purpose |
|----------|---------|
| `GAMEGATE_API_TOKEN` | Shared secret; write endpoints require it as `X-GameGate-Token` |
| `GAMEGATE_DB_PATH` | SQLite file location |
| `GAMEGATE_URGENT_BREAKTHROUGH` | Do urgent events interrupt gaming? (`true`/`false`) |
| `DISCORD_BOT_TOKEN`, `GAMEGATE_DISCORD_CHANNEL_ID` | Discord connector |
| `GMAIL_ENABLED`, `GMAIL_VIP_SENDERS`, `GMAIL_TOKEN_PATH` | Gmail connector — see [docs/GMAIL_SETUP.md](docs/GMAIL_SETUP.md) |
| `SLACK_ENABLED`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | Slack connector — see [docs/SLACK_SETUP.md](docs/SLACK_SETUP.md) |
| `CLASSIFIER_ENABLED`, `CLASSIFIER_MODEL`, `OPENAI_API_KEY` | Optional AI classifier |

**No secrets are ever committed.** `.env`, OAuth tokens, and `agent/config.json` are gitignored.

## Run the detector (gaming PC)

```bash
cd agent
cp config.example.json config.json    # set server URL, token, and your game process names
pip install psutil
python detector.py                    # or --once for a single debug poll
```

## Deployment (Linux + Nginx)

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): Uvicorn manually → systemd service → Nginx proxy, with a verification command at each layer. Nginx listens on the network; Uvicorn stays on `127.0.0.1`.

## Known limitations

- SQLite = single host, modest concurrency (fine for one user; PostgreSQL is the documented scale-up path)
- Gmail uses polling, not push; worst-case latency is one poll interval
- Slack ingests `app_mention` only (first pass, by design)
- The detector matches process names; games launched under unexpected binary names need adding to `config.json`

## Security & privacy

Write endpoints are token-authenticated. Gmail access is read-only; only safe snippets are stored, never full bodies. The AI classifier receives sender/title/snippet only. Data lives in one SQLite file you can delete at any time. See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for operational notes.

## Documentation

[ARCHITECTURE.md](ARCHITECTURE.md) · [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) · [docs/GMAIL_SETUP.md](docs/GMAIL_SETUP.md) · [docs/SLACK_SETUP.md](docs/SLACK_SETUP.md) · [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) · [docs/DEMO.md](docs/DEMO.md)
