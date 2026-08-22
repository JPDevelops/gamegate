# GameGate Architecture

A lightweight detector on the gaming PC watches running processes and reports state transitions (gaming/available) to a FastAPI service over HTTP. Connectors for Gmail, Slack, and Discord normalize incoming messages into one internal Event model, stored idempotently in SQLite. A routing engine combines the current availability state with each event's priority to decide: deliver now (urgent break-through) or queue. When a gaming session ends, queued events become a single post-game digest. An optional LLM classifier improves prioritization but always falls back to deterministic rules — the service never depends on it.

Current layering in `app/`:

- `models/` — data shapes (Pydantic)
- `services/` — logic and state
- `api/` — thin HTTP routes

*This is a placeholder summary. The full architecture document — normalization rationale, idempotency design, routing rules, deployment topology, and tradeoffs — is written at runbook Step 15.*
