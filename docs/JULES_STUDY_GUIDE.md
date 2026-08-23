# Jules' Study Guide

*The build went autonomous on the Product Owner's instruction — this guide is how you own it anyway. Read one section per sitting. For each, open the file it names and find the thing it describes. The interview answers are outlines to internalize and say in your own words, not scripts to recite.*

## Part 1 — The tour (match each to its file)

| Piece | File | One-liner you should be able to say |
|-------|------|-------------------------------------|
| The API | `app/main.py` | "FastAPI turns functions into HTTP endpoints; this wires the routers together" |
| Data shapes | `app/models/` | "Pydantic models validate at the boundary — bad input 422s before touching logic" |
| All SQL | `app/services/repositories.py` | "One file owns the database; swap SQLite→Postgres here and nothing else moves" |
| The brain | `app/services/routing.py` | "Pure function: state × priority → deliver/queue/suppress. No I/O = trivially testable" |
| The digest | `app/services/digest_service.py` | "Deterministic: same events in, same digest out" |
| Sessions | `app/services/status_service.py` | "Entering gaming opens a session; leaving closes it and fires exactly one digest" |
| The AI | `app/services/classifier.py` | "Behind an interface, schema-validated, always falls back — never a point of failure" |
| Translators | `app/integrations/` | "Each service's weird payload becomes our one Event shape at the door" |
| Your PC's agent | `agent/detector.py` | "Watches processes, reports only transitions, retries when the server's down" |
| The gate | `app/security.py` | "Writes need the shared token; reads are open" |

## Part 2 — The five principles this codebase runs on

1. **Fail loudly and early.** Bad data is rejected at the boundary with a clear error, never absorbed to explode later. (You learned this one live — the "napping" 422.)
2. **Normalize at the edge.** Core logic never sees Gmail-shaped or Slack-shaped data. One Event model in, one set of rules.
3. **Idempotency everywhere.** External services WILL redeliver. `(source, external_id)` uniqueness makes replays free; ack-once makes double-delivery impossible.
4. **Pure logic is testable logic.** The routing table is a function with no I/O — that's why all 16 rows + policy variants are exhaustively tested.
5. **AI is a guest, not a landlord.** Validated like any untrusted input, timeout-bound, and the deterministic fallback means the product works with the LLM unplugged.

## Part 3 — The 20 interview questions, answer outlines

1. **What problem does GameGate solve?** Protects focus while gaming: holds non-urgent Gmail/Slack/Discord, lets true emergencies through, one catch-up digest after.
2. **Trace a Gmail message to the digest.** Poller fetches → deterministic rules assign priority → normalized to Event → POST /events (idempotent store) → routing says QUEUE (I was gaming) → game ends → session closes → digest built from queued events, marks them consumed → Discord connector delivers it.
3. **Why one Event model?** So core logic has one code path. Otherwise every rule needs per-service branches; adding a source would mean touching everything.
4. **What is FastAPI doing?** HTTP layer: maps URLs to functions, validates requests via Pydantic, gives /docs. It's how detector, connectors, and I talk to the core from different machines.
5. **Why Nginx in front of Uvicorn?** Nginx owns the network edge (hardened listener, limits, forwarded headers, TLS termination); Uvicorn stays on localhost running the app. One job each; app restarts don't drop the listener.
6. **How does the detector communicate?** HTTPS POST /status with the shared token, transitions only (debounced), retry-next-cycle when the API is down — nothing lost, nothing spammed.
7. **How do you prevent double-processing?** DB uniqueness on (source, external_id): replays return the original (200 vs 201). Delivery side: ack-after-send, ack-once.
8. **What if Discord is down?** Core keeps ingesting/routing. Pump can't send → doesn't ack → items stay pending → retried next cycle. Nothing crashes, nothing double-posts on recovery.
9. **What if the LLM is down/bad?** SafeClassifier catches everything — timeout, outage, invalid JSON, schema violation — and deterministic rules answer instead. Tested per failure mode.
10. **Deterministic vs AI decisions?** Deterministic: all routing, digest, dedup, sessions, connector priority rules. AI: optional enrichment (category/urgency/summary/suggested action). System is fully functional with AI off.
11. **How do you test integrations without live services?** Interfaces + fakes: FakeGmail, fake API clients, httpx.MockTransport for the LLM. CI runs the whole suite offline in ~20s.
12. **Unit vs integration test here?** Unit: routing table rows, email rules (pure functions). Integration: TestClient + real temp SQLite through the whole API path (e.g. duplicate POST, restart survival).
13. **What does GitHub Actions do?** Every push/PR: install → ruff → pytest. A red run blocks the merge, so main is always shippable.
14. **Why branches/PRs solo?** The diff review habit + green-CI gate + a history that explains WHY every change happened. Solo now, team-ready workflow forever.
15. **How are secrets managed?** .env (gitignored) + .env.example with names only; OAuth token files gitignored + chmod 600; if a secret ever touches git history, rotate it — deletion isn't enough.
16. **What scopes and why?** Gmail: readonly (we never send/delete). Slack: app_mentions:read + chat:write only. Discord: View/Send/Read History. Least privilege — a leak of any token has minimal blast radius.
17. **10,000 users — first redesign?** SQLite → PostgreSQL (behind the existing repositories), then a queue between ingestion and routing, per-user config, real auth (per-client creds), Gmail push instead of polling.
18. **A decision you changed?** Idempotency key: started as external_id alone; the PM review caught that two services can collide → (source, external_id). (Check PR #13's description.)
19. **What did AI help with, how did you validate?** AI paired/implemented; validation = acceptance criteria per issue, tests for every behavior, CI, and diff review. I can trace any feature to its issue, PR, and tests.
20. **Most confident debugging under pressure?** Pick routing/digest: pure logic, exhaustive tests, and the journalctl anchors (`state transition`, `session closed`) walk you straight to any failure.

## Part 4 — Do these yourself (in order, ~30 min each)

1. Clone fresh, follow README from zero, run the tests. Fix nothing — just verify the docs are honest.
2. Break a routing test on purpose (flip one expected value), watch CI go red on a PR, revert.
3. Add a game to `agent/config.example.json`, run detector with `--once` against a local API, watch the transition log.
4. Read PR #13 and #15 top to bottom — they're the two answers most likely to be probed.
5. Run the demo script (docs/DEMO.md) end to end once, alone, before doing it for your step dad.
