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
| **M21** Tkinter windows created from worker threads | CORRECTED RATIONALE: an earlier note claimed "cards are sequential and callbacks are rare" — that's wrong. `update_check_loop` is a *separate thread* from `pump_loop`, so Tk `mainloop()` runs on a non-main thread unconditionally, and a notification arriving during an update prompt means two Tk interpreters in two threads. The real reason to defer is only that the correct fix (a single UI thread + a `queue.Queue`) is a Windows-only refactor that cannot be exercised in this Linux CI and must be verified by hand on Windows — not that the risk is small. The update prompt is already gated to non-gaming moments (M22), which narrows the window but does not remove it. | A Windows test pass: move pystray off the main thread (`run_detached`), run Tk on the main thread, workers post render requests onto a queue. |
| **M23** toast notifier ignores `duration_s`/`sound` | The Windows toast backend (`windows-toasts`) doesn't expose per-toast duration; honoring `sound` needs a code path we can't test headless. The custom overlay (the default notifier) already honors both. | When the toast backend gains the controls, or drop toast mode. |
| **M24** CSP allows `unsafe-inline` because the dashboard uses inline `onclick` | Moving ~20 handlers to `addEventListener` and dropping `unsafe-inline` is a self-contained but sizable template change; scheduled as its own PR so it can be reviewed cleanly. | Next dashboard hardening PR. |
| **M26** dependencies unpinned / no lockfile | Adds a lockfile + CI install-from-lock; low risk but changes the CI pipeline, kept separate. | Next CI PR. |
| **#27 / #45** scoped tokens / per-service env | Single-user, single-trust-level today; no boundary to enforce yet. | First multi-user or multi-host deployment. |

Assorted nitpicks (trailing newlines, a bound-port mutex, keyword word-boundaries,
etc.) are tracked in the review report at `reviews/dad-simulation-2026-08-24.md`
and folded into the batches above where cheap.

## Independent review #2 (2026-08-24) — status

**All 3 blockers and all 13 majors fixed**, each with a test that fails on the
old code where testable: B1 session-close race (was falsely documented-fixed —
now genuinely atomic + concurrency test + corrected pentest doc), B2 cleartext
:80 (redirects to HTTPS, live), B3 false claims (dead config removed, README /
DESKTOP_APP / website corrected), M1 Gmail-connect regression, M2 datetime 500,
M3 XFF spoof (+ real test), M4 open /docs, M5 token in journald (live), M6
threading rationale, M7 overlay retry loop + poison guard, M8 dashboard input
wipe, M9 CSP artwork, M10 Gmail grant revoke, M11 client 401 logging, M12 atomic
.env write, M13 systemd sandboxing.

**Minors fixed:** N2 (dedup get_settings_service), N5 (cap bulk-unread ids),
N9 (guard non-numeric guild id), N19 (winreg ImportError on non-Windows).

**Minors intentionally left** (small / by-design / document-only): naive keyword
substring matching (N14, cheap and deliberate), Discord DM branch reachable only
in tests (N16/N17), process-name keying (N18), `_pending_states` process-local
(N6, single-worker), and assorted style nitpicks. None are bugs or security
issues. Systemd hardening is committed to the units but not yet live-applied
(needs a tested restart window).

## Independent review #3 (2026-08-24) — status

Cleaner than #2 (3 blockers / 14 majors vs 3 / 23), and the backend + the honesty
record were praised. **Fixed:** B1 notification retry loop (ack failures now
count; render-once; tests on both drop paths), B2 DEPLOYMENT.md made runnable
(conf.d log format, TLS vhost, certbot in the ladder, correct paths), M2 toast
honors duration/sound via winotify (+ corrected the wrong-library note), M3
ARCHITECTURE/DEMO (desktop app is the notifier, not Discord), M4 status-field
log injection (sanitized + capped + test) and its false pentest claim, M5
GMAIL_SETUP.md (web client type, real token path, dead VIP env removed),
config.example.json https, bounds on `undelivered()`/`application`/`game`, and
the site test count changed to a non-rotting "160+".

**Deferred with a concrete plan — B3 (remove `sudo` from the web tier):** the
API currently runs with passwordless `sudo systemctl` so a dashboard button can
start/stop connectors, which means `NoNewPrivileges` can't be set on that unit.
This is a real design change, not a bug fix, and touches the live connector
enable/disable flow, so it is scheduled rather than rushed. **Plan:** the
connector units run always (systemd-enabled) and poll their `*_ENABLED` flag
(already written to `.env` by `update_env_var`) to decide whether to do work;
`service_active()` reads the flag instead of `systemctl is-active`; the API drops
`sudo` entirely and the unit gets `NoNewPrivileges=true` with `ReadWritePaths`
narrowed to the data dir. Until then it is documented, and the connector units
already set `NoNewPrivileges=true`.
