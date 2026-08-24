# Adversarial review — follow-up tracker

An external senior-engineer review (2026-08-24) raised ~80 findings. This tracks
what was fixed and what is deliberately deferred, with reasons.

## Fixed (across review batches 1–4)

**Blockers:** `.env` load + fail-closed startup guard; deleted the interview
study guide for honest engineering notes; classifier described accurately;
website/doc claims reconciled with the code.

**Security:** signed session cookie (no longer the raw token) + logout; security
headers now wrap 429s; nginx access log strips the `?key=` token; constant-time
compare consolidated; TLS config committed; rate-limiter memory now bounded.

**Correctness:** `find_by_id` (no 1000-row scan); no lost break-through
notifications; game-switching mid-session opens a fresh session/recap;
single source of truth for VIP senders (server-side); settings panel shows live
values; connector failures surface instead of faking success; ingest no longer
mutates the caller's model; httpx clients are closed.

**Quality/CI:** real ruff rule set enforced on app + agent; deleted dead code
and a tautological test; migrations re-raise real errors; systemd unit paths
made consistent; hermetic test env.

## Deferred (with reasons)

| Finding | Why deferred | Revisit |
|---|---|---|
| **M21** Tkinter windows created from worker threads | The fix (a single UI thread + a work queue) is a real refactor of Windows-only code that can't be exercised in CI; today the cards are sequential in the pump's own loop and the tray callbacks are rare. | Before adding any concurrent UI surface; do it with a manual Windows test pass. |
| **M23** toast notifier ignores `duration_s`/`sound` | The Windows toast backend (`windows-toasts`) doesn't expose per-toast duration; honoring `sound` needs a code path we can't test headless. The custom overlay (the default notifier) already honors both. | When the toast backend gains the controls, or drop toast mode. |
| **M24** CSP allows `unsafe-inline` because the dashboard uses inline `onclick` | Moving ~20 handlers to `addEventListener` and dropping `unsafe-inline` is a self-contained but sizable template change; scheduled as its own PR so it can be reviewed cleanly. | Next dashboard hardening PR. |
| **M26** dependencies unpinned / no lockfile | Adds a lockfile + CI install-from-lock; low risk but changes the CI pipeline, kept separate. | Next CI PR. |
| **#27 / #45** scoped tokens / per-service env | Single-user, single-trust-level today; no boundary to enforce yet. | First multi-user or multi-host deployment. |

Assorted nitpicks (trailing newlines, a bound-port mutex, keyword word-boundaries,
etc.) are tracked in the review report at `reviews/dad-simulation-2026-08-24.md`
and folded into the batches above where cheap.
