# GameGate Architecture

## The one-paragraph version

A lightweight detector on the gaming PC watches running processes and reports state transitions to a FastAPI service. Connectors for Gmail and Discord (Slack is written but disabled in v0.1) normalize incoming messages into one internal Event model, stored idempotently in SQLite. A routing engine combines the current availability state with each event's priority to decide: deliver now, queue, or suppress — using deterministic rules (VIP senders, urgent keywords). When a gaming session ends, queued events become exactly one post-game digest, delivered to Discord. An optional LLM classifier endpoint demonstrates a graceful-fallback pattern but is not yet wired into the automatic ingest path.

## Why external services normalize into one Event model

Gmail messages, Slack mentions, and Discord pings look nothing alike on the wire. If their raw shapes leaked into the core, every business rule would need three code paths (`if source == gmail…`). Instead, each connector translates its payload into the same `Event` shape at the boundary (`app/integrations/*`). The routing engine, digest builder, and storage never know or care where an event came from — adding a fourth source means writing one new adapter, not touching the core.

## How status and sessions are represented

One current status row (state + application + started_at) and an append-only `sessions` table. `StatusService.set()` is the only writer: a transition **into** `gaming` opens a session; a transition **out of** `gaming` closes it, computes duration, and triggers digest creation. Everything else is just a status update. The four states are a closed enum — any other value is rejected with a 422 at the API boundary ("fail loudly and early").

## Routing: deliver now vs. queue

`app/services/routing.py` is a pure function — state × priority → decision — with no I/O, so the entire policy table is unit-tested exhaustively:

| State | urgent | actionable | informational | ignore |
|-------|--------|-----------|---------------|--------|
| available | deliver | deliver | deliver | suppress |
| focused | deliver | queue | queue | suppress |
| gaming | deliver* | queue | queue | suppress |
| away | queue | queue | queue | suppress |

\* configurable break-through policy — a per-user setting (`urgent_breakthrough`) stored in the DB and edited in the dashboard, passed into `decide()` at ingest time.

Deliver-now events go to a `notifications` queue that the Discord connector drains (send → then ack). Queued events wait for the digest. Suppressed events are stored (audit trail) but consumed immediately.

## How idempotency works

External services redeliver: Gmail re-polls see the same messages, Slack retries slow acks, webhooks double-fire. Two layers absorb this:

1. **Ingestion**: events are unique on `(source, external_id)`. A replay returns HTTP 200 with the original record (vs. 201 for a create) and stores nothing. Source is part of the key because two services may coincidentally share id formats.
2. **Delivery**: notifications and digests are acked *after* a successful send, and acking is once-only. A deliver-now event queues its notification and only then is marked consumed, so a crash between those writes leaves it in the digest queue — at worst it surfaces twice (a live notification and a digest line), never lost. A crashed connector re-sends at worst; the per-item ack guard prevents the same notification or digest posting twice.

The digest itself is idempotent by construction: building it consumes the queued events (marks them `delivered` with the digest id), so a second session end finds an empty queue.

## Where persistence happens

SQLite, accessed exclusively through `app/services/repositories.py` — routes and services never contain SQL. Five things persist: events (with routing outcome), current status, gaming sessions, digests, and pending notifications. Tests prove restart survival by re-opening the same database file. Tradeoff, documented deliberately: SQLite is single-host with modest write concurrency — correct for a personal service; the repository layer is the seam where PostgreSQL would slot in at scale.

## Why the LLM is optional and untrusted

The classifier (`app/services/classifier.py`) sits behind an interface. Its output is schema-validated like any untrusted API response — invalid JSON, out-of-range urgency, timeouts, and outages all raise, and `SafeClassifier` catches *everything* and falls back to deterministic rules. The system's correctness never depends on the model being up, honest, or affordable. Tests cover each failure path with mocked transports; no test spends money.

## Why Nginx sits in front of FastAPI

Uvicorn binds `127.0.0.1:8000` and is never exposed. Nginx owns the network edge: it's the hardened listener, enforces request/body limits, adds forwarded-for headers, and terminates TLS (live on 443 via Let's Encrypt — `nginx/gamegate-tls.conf`). Each component does one job: Nginx speaks "internet," Uvicorn speaks "application." This also means the app can restart (systemd `Restart=on-failure`) without the listener disappearing.

## Tradeoffs and what changes at scale

- **Polling Gmail** (simple, no public endpoint) → push notifications via Pub/Sub at scale
- **SQLite** → PostgreSQL behind the same repositories; add real migrations
- **Connector processes on one box** → a queue (e.g. Redis) between ingestion and routing once there are many users
- **Shared-token auth** (lab-grade, one user) → per-client credentials + webhook signature validation for real inbound traffic
- **Single digest channel** → per-user preferences and delivery schedules
