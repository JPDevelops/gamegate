# Engineering notes

How GameGate is built, the decisions behind it, and the bugs I hit running it
on real hardware. Written so a new reader (or me, six months from now) can
understand *why* the code looks the way it does.

## How AI was used, and how I own it

I built GameGate as a learning project with heavy AI pair-programming: I set the
requirements and acceptance criteria, worked ticket by ticket, reviewed every
diff, and merged. The AI wrote a lot of the code; I directed it, tested it,
broke it on my own machine, and fixed what broke. The parts I'm proudest of are
the ones I can explain end to end and the bugs I found by actually running the
thing — those are documented below and in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
I'd rather show you a real bug I found at 11pm than recite a feature list.

## The tour — where everything lives

| Piece | File | What it does |
|-------|------|--------------|
| API wiring | `app/main.py` | FastAPI app: routers, middleware order, startup guard |
| Data shapes | `app/models/` | Pydantic models — bad input 422s before touching logic |
| All SQL | `app/services/repositories.py` | The only file with queries; swap SQLite→Postgres here |
| Routing core | `app/services/routing.py` | Pure function: state × priority → deliver/queue/suppress |
| Digest | `app/services/digest_service.py` | Deterministic: same events in, same digest out |
| Sessions | `app/services/status_service.py` | Entering gaming opens a session; leaving fires one digest |
| Classifier | `app/services/classifier.py` | Optional LLM behind an interface, always falls back |
| Connectors | `app/integrations/` | Each service's payload → our one Event shape |
| PC agent | `agent/detector.py` | Watches processes, reports transitions, retries on failure |
| Auth | `app/security.py` | Token on every data endpoint; signed session cookie for the dashboard |

## Principles the codebase runs on

1. **Fail loudly and early.** Bad data is rejected at the boundary with a clear
   error, never absorbed to explode later.
2. **Normalize at the edge.** Core logic never sees Gmail- or Discord-shaped
   data — one Event model in, one set of rules.
3. **Idempotency in the schema, not by convention.** `(source, external_id)`
   uniqueness makes replays free; the design assumes redelivery instead of
   fighting it.
4. **Pure logic is testable logic.** The routing table has no I/O, which is why
   all 16 rows plus policy variants are exhaustively tested.
5. **AI is a guest, not a landlord.** The classifier is validated like any
   untrusted input, timeout-bound, and always falls back to deterministic rules.

## Decisions I changed my mind on

- **Idempotency key.** Started as `external_id` alone; a review caught that two
  services can collide on id format → `(source, external_id)`. (PR #13.)
- **Session cookie.** First version stored the API token itself in the cookie.
  A security review pointed out a cookie leak would then be a full credential
  compromise, so it's now a signed, expiring session token derived from — but
  not equal to — the master token. (Review batch 1.)
- **Fail-closed startup.** The documented `cp .env.example .env` setup once
  loaded no `.env` at all, so an unset token silently disabled auth. Now the
  app loads `.env` and refuses to start without a token unless you explicitly
  opt into dev mode. I found this reviewing my own setup path.

## Bugs I found by running it (the real story)

These are field-verified — each one is a specific failure I hit on real
hardware and fixed. Full detail in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

- **The card parade.** First Gmail connect ingested 31 old unread emails and
  every one popped an overlay. Fix: a *freshness* rule — an event received long
  before ingestion is history, not an interruption, so it queues for the digest
  regardless of priority (`ingest_service.py`).
- **Wallpaper Engine looked like a game.** `wallpaper64.exe` tripped the process
  detector as GAMING. Fix: an exclusion list + a regression test using the real
  process name (`agent/detector.py`, `tests/test_detector.py`).
- **DPI scaling broke the overlay.** The card measured wrong on high-DPI
  displays until height was computed from measured text bounds, not a line
  count (`agent/overlay.py`).
- **Self-notifications.** My own Discord messages notified me. Fix: skip the
  owner's id.
- **Frozen-path packaging.** PyInstaller's `sys._MEIPASS` broke relative paths;
  fixed and covered by a test for the frozen case.

## Scale-up path

For many users: SQLite → PostgreSQL behind the existing repository layer, a
queue between ingestion and routing, per-user config and per-client auth, and
Gmail push instead of polling. The repository boundary is what makes the
database swap credible — nothing outside `repositories.py` touches SQL.

## What isn't done yet (honest limitations)

- **Slack** connector code exists but is disabled in v0.1 (`/connect/slack`
  returns 409 by product decision). Gmail and Discord are the shipped sources.
- **AI classifier** is an endpoint that demonstrates the fallback pattern; it is
  not yet wired into the automatic ingest path (ingestion uses deterministic
  rules).
- Single-host SQLite; Gmail polling latency; process-name matching needs new
  binaries added to config. See README "Known limitations."
