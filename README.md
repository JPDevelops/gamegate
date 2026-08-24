# GameGate

## Project goal

GameGate is an app that tracks when you play games — it looks at your system processes. From there, while playing, it holds non-urgent messages from Gmail and Discord (Slack is planned) until you're done playing; urgent messages break through. When the session ends, you get one digest of everything you missed.

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

Every external message becomes one internal **Event** `(source, external_id, sender, title, priority, …)`, stored idempotently — the same message can never be ingested twice. The **routing engine** combines your current state (available / focused / gaming / away) with the event's priority to deliver immediately, queue for the digest, or suppress. Ingestion prioritizes with deterministic rules (VIP senders, urgent keywords). An optional **AI classifier** endpoint (`POST /events/{id}/classify`) can score an event's urgency and demonstrates the graceful-fallback pattern — it is not yet wired into the automatic ingest path.

## Stack

- **Python 3.12**, **FastAPI + Uvicorn** — API layer
- **SQLite** — persistence behind a repository layer
- **Pytest + httpx** — 160+ tests, all offline (fakes/mocks for every external service)
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
| `GAMEGATE_API_TOKEN` | Shared secret; all data endpoints require it as `X-GameGate-Token` (mandatory in production) |
| `GAMEGATE_DB_PATH` | SQLite file location |
| *(urgent break-through)* | Whether urgent events interrupt gaming is a per-user setting in the dashboard (stored in the DB), not an env var |
| `DISCORD_BOT_TOKEN`, `GAMEGATE_DISCORD_CHANNEL_ID` | Discord connector |
| `GMAIL_ENABLED`, `GMAIL_TOKEN_PATH` | Gmail connector — see [docs/GMAIL_SETUP.md](docs/GMAIL_SETUP.md). VIP senders are set in the dashboard Settings (stored in the DB), not via env. |
| `STEAMGRIDDB_API_KEY` | Optional: game artwork lookups for the desktop app (`/art`) |
| `SLACK_ENABLED`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | Slack connector — see [docs/SLACK_SETUP.md](docs/SLACK_SETUP.md) |
| `CLASSIFIER_ENABLED`, `CLASSIFIER_MODEL`, `OPENAI_API_KEY` | Optional AI classifier |

**No secrets are committed.** `.env`, OAuth tokens, and `agent/config.json` are
gitignored, and CI runs a [gitleaks](https://github.com/gitleaks/gitleaks) scan
over the full git history on every push — so this is an enforced check, not just
a claim.

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
- Slack is **not enabled in v0.1** (the connector code exists but `/connect/slack` returns 409 by product decision); Gmail and Discord are the shipped sources
- The detector matches process names; games launched under unexpected binary names need adding to `config.json`

## Security & privacy

All data endpoints (reads included — they carry message content) are token-authenticated. The only unauthenticated routes are `/health`, the OAuth callback (protected by a single-use state), and `/logout`; the interactive `/docs`/`/openapi.json` are enabled in development only and disabled in production. Gmail access is read-only; only safe snippets are stored, never full bodies. The AI classifier receives sender/title/snippet only. Data lives in one SQLite file you can delete at any time. See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for operational notes.

## Engineering decisions

Five choices that shaped the system, and their tradeoffs:

1. **One internal Event model; per-service adapters at the edge.** Gmail, Slack, and Discord payloads never reach core logic — each connector translates at the boundary. Cost: an adapter per service. Payoff: routing/digest/storage have exactly one code path, and adding a source touches nothing else.
2. **Idempotency as a schema guarantee, not a convention.** `(source, external_id)` uniqueness makes redeliveries free (replays return the original, 200 vs 201), and delivery acks are once-only after a successful send. External services redeliver constantly — the design assumes it instead of fighting it.
3. **A custom overlay instead of native Windows toasts.** Focus Assist silences toasts during fullscreen gaming — precisely when an urgent break-through matters. The custom always-on-top card is immune, never steals focus, and sizes itself to measured text. Tradeoff: we own the rendering (and found the DPI bugs that come with that).
4. **The LLM is a guest, never a dependency.** The classifier sits behind an interface, its output is schema-validated like any untrusted API, and every failure path lands on deterministic rules. The product works with AI unplugged; tests never make a paid call.
5. **Freshness gates interruptions.** An event received long before ingestion is history, not an interruption — it's queued silently instead of shown, regardless of priority. Learned live: the first Gmail sync tried to pop 31 overlay cards for old mail.

## Documentation

[ARCHITECTURE.md](ARCHITECTURE.md) · [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) · [docs/GMAIL_SETUP.md](docs/GMAIL_SETUP.md) · [docs/SLACK_SETUP.md](docs/SLACK_SETUP.md) · [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) · [docs/DEMO.md](docs/DEMO.md)
