# Adversarial review — follow-up tracker

An external senior-engineer review (2026-08-24) raised ~80 findings. This tracks
what was fixed and what is deliberately deferred, with reasons.

## Fixed (across review batches 1–4)

**Blockers:** `.env` load + fail-closed startup guard; replaced the old study
guide with honest engineering notes; classifier described accurately;
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
| **M21** Tkinter windows created from worker threads | **Concrete crash fixed; ideal design still pending a Windows pass.** The specific danger both reviewers named — a notification arriving during an update prompt creating TWO live Tk interpreters in two threads — is now prevented: `overlay.py` serializes all rendering behind a module `_ui_lock`, so at most one `tk.Tk()` root exists at any moment (a card that arrives while a prompt is up just waits, never dropped — blocking acquire, so the pump's poison-guard can't mistake "UI busy" for "failed"). A concurrency test asserts the impls never overlap. The *ideal* end-state (pystray `run_detached`, one Tk root on the main thread, workers posting render requests to a `queue.Queue`) is still worth doing but is a Windows-only refactor not exercisable in Linux CI. | Owner smoke-test on Windows after updating; optional later move to a single main-thread UI loop. |
| **M23** toast notifier ignores `duration_s`/`sound` | FIXED (review 3): the code uses `winotify`, which exposes `duration` and `set_audio`; both are now wired through, and `winotify` is a declared dependency and installed by the release build. | Done. |
| **M24** CSP allows `unsafe-inline` because the dashboard uses inline `onclick` | Moving ~20 handlers to `addEventListener` and dropping `unsafe-inline` is a self-contained but sizable template change; scheduled as its own PR so it can be reviewed cleanly. | Next dashboard hardening PR. |
| **M26** dependencies unpinned / no lockfile | Adds a lockfile + CI install-from-lock; low risk but changes the CI pipeline, kept separate. | Next CI PR. |
| **#27 / #45** scoped tokens / per-service env | Single-user, single-trust-level today; no boundary to enforce yet. | First multi-user or multi-host deployment. |

Assorted nitpicks (trailing newlines, a bound-port mutex, keyword word-boundaries,
etc.) were folded into the batches above where cheap.

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

## B2/B3 sudo removal — code DONE (live-apply pending)

The web API no longer shells out to `sudo systemctl`. Connect/disconnect just
flip a per-connector `*_ENABLED` flag in `.env`; the connector processes
(gmail_run, discord_bot) run continuously and read that flag live to start/stop
their own work, and `service_active()` reports connected-state from the flag (no
subprocess). The API unit now sets `NoNewPrivileges=true`. Fully tested offline.

**Live-apply is a coordinated step (not auto-deployed):** on the server the
connector units must run continuously (`systemctl enable --now gamegate-gmail
gamegate-discord`), the live `.env` must set `GAMEGATE_DISCORD_ENABLED=true` so
Discord keeps ingesting, the new `deploy/gamegate.service` must be installed
(daemon-reload + restart), and the passwordless sudoers rule can then be removed.
Deploying the API alone without these would leave the dashboard toggles flipping
a flag the old connector code ignores — so this is done with the owner watching.

## Review round 8 (two independent no-context reviewers) — 2026-08-24

A cross-model pass (OpenAI gpt-5.6-sol) plus a fresh Claude reviewer, each given
the whole repo + site with no prior context. Both returned **0 blockers**.

**Fixed this round (correctness/security):**
- **Recap backlog-starvation** (the important one): the recap filtered
  `undelivered()`'s 1000 oldest rows in Python *after* the LIMIT. Because
  away/focused events are never consumed under decision C, they accumulate
  unbounded and, past 1000, would starve a game's real in-window messages out of
  its recap. Now filtered in SQL (`undelivered_in_window`) so the cap bounds the
  rows that matter. Regression test with a 1001-row backlog.
- **OAuth callback expiry**: `gmail_callback` checked state membership but never
  the stored TTL (cleanup only ran when a new state was issued). Now pops and
  checks expiry at callback. Test with a past-TTL state.
- **`.env` concurrent-writer race**: fixed `.env.tmp` + unlocked read-modify-write
  could lose an update or collide on `os.replace`. Now serialized under an flock
  with a unique `mkstemp` temp file. 24-thread test (old code throws).
- **Concurrency test guarded dead code**: the race test hit
  `SessionRepository.close_current()`, which production never calls. Deleted that
  dead method; the tests now race the real `StatusService.set(available)` path
  and assert the true invariant (one closed session, one recap).
- **`started_at` normalization**: naive→UTC + implausible-future clamp at the
  model boundary, so a bad detector clock can't open a session with an absurd
  recap window.

**Fixed this round (doc/claim accuracy & polish):**
- Sidebar version was hardcoded `v0.1` on a 0.2.0 app — now injected from
  `__version__` (test-guarded); DEPLOYMENT health example corrected.
- Static `/site/` now carries the full security-header set (HSTS/CSP/
  Permissions-Policy) at the nginx edge, not just three headers.
- Reconciled several docs to the code: port 80 is redirect-only (not "kept for
  plain-HTTP lab clients"); transactional session close is DONE (not "open
  debt"); ARCHITECTURE persists 7 stores (not 5) and is honest about the
  deliberate non-repository SQL and the real cost of a Postgres port; recap is
  delivered by the desktop app (not the Discord connector); the self-updater
  only works for a source install (a downloaded exe needs a signed artifact).
- Removed job/interview/"dad-simulation" meta-artifacts and a dangling review
  link from the docs.
- `gitleaks` secret-scan job now runs on every push (full history) — the
  "no secrets" claim is an enforced check, verified clean across 131 commits.
- Smaller: settings version only bumps on a real change; art negative-cache test
  counts provider calls; release bundles the build stamp into the exe; test_health
  uses the shared fixture; marketing wording made precise (dashboard-managed
  connectors; Focus-Assist-immune but not exclusive-fullscreen-immune overlay).

**Deferred (with reasons) — needs the owner or a Windows pass, not a guess:**
| Finding | Why deferred | Needs |
|---|---|---|
| **Dashboard-DND vs detector state machine** (a manual DND set from the dashboard can be silently re-overridden — or fail to re-assert — because the detector short-circuits when its locally-remembered game/state is unchanged) | The correct fix is a design change: model manual DND as a distinct persisted override and derive the effective state server-side, rather than DND competing as an availability value. That changes detector + dashboard + server behavior and UX, and can only be validated on Windows. | An owner product decision on override semantics + a Windows test pass. |
| **Tkinter windows created from worker threads** (M21) | Correct fix (single UI thread + a render queue) is a Windows-only refactor not exercisable in Linux CI. | Manual Windows verification. |
| **Unpinned dependencies / no lockfile** (M26) | Adds a lockfile + install-from-lock; low risk but changes the CI pipeline; kept as its own PR. | A dedicated CI PR. |
| **CSP `unsafe-inline`** (M24) | Moving ~20 inline handlers to `addEventListener` is a sizable, self-contained template change. | A dashboard-hardening PR. |
| **Process-name detection determinism / hysteresis** | Rank duplicate-named processes deterministically and add switch hysteresis; desktop-only, needs care to avoid detection regressions. | A desktop change + manual test. |

## Review round 9 (two more independent no-context reviewers) — 2026-08-24

OpenAI gpt-5.6-sol + a fresh Claude reviewer. Both found **0 backend blockers**;
the Claude reviewer's only MAJOR was the already-known/deferred Tkinter threading.

**Fixed this round:**
- **Timestamp UTC normalization** (both reviewers): `Event.received_at` and
  `StatusUpdate.started_at` relabeled naive datetimes to UTC but kept aware
  non-UTC offsets verbatim, so the recap's ISO-string window query could
  misorder a `-07:00` timestamp and drop it from a recap. Now `astimezone(UTC)`
  at the model boundary; offset-crossing regression test fails on the old code.
- **Discord resend storm**: the Discord `DeliveryPump` re-posted every cycle when
  a send succeeded but the ack failed. Added a per-process "already sent" set
  (the guard the desktop pump already had) + a test; corrected ARCHITECTURE to
  state delivery is honestly at-least-once.
- **Discord ingestion scope**: added an opt-in `GAMEGATE_DISCORD_INGEST_CHANNELS`
  allowlist so the bot needn't ingest every readable channel (unset = prior
  behavior, so no live change until the owner opts in).
- **Gmail 200-cap** could fetch up to 250 (150 + a 100 page): request only the
  remaining room and slice.
- **OAuth token file** now written atomically (temp + fsync + `os.replace`),
  matching the `.env` writer.
- **Auth throttle** no longer counts a keyless `/app` login-page view, so the
  owner can't lock their own IP by refreshing a bookmarked dashboard (a real
  `?key=` guess still counts). Test.
- **Robustness**: `detector.load_config` falls back to defaults on malformed
  JSON instead of a raw startup traceback (test); the tray pump guards missing
  `id`/`state` keys instead of aborting the whole cycle.
- **Doc/claim accuracy**: "queued waits for the digest" → the two real fates
  (recap if in-session, else Messages tab); "SQL lives only in repositories" /
  "Postgres is a drop-in" toned down to the truth across ENGINEERING_NOTES,
  ARCHITECTURE, and the module docstring; dashboard connector status described as
  configured-state (not live health); the honest caveat that the master token
  travels once in the opening `?key=` URL; "Pydantic on every body" → "typed
  models or explicit validation"; version labels v0.1 → v0.2.
- **Quality**: rewrote a vacuous XFF test to actually exercise last-hop
  selection from a trusted peer; sha256-verify the gitleaks CI download; removed
  duplicate nginx server-level headers; stripped internal agent codenames
  (Vega/Orion/Nebula) from code comments.

**Raised with the owner (a product call, not a code defect):**
- **"Notification gate" framing** (OpenAI called it a BLOCKER; the Claude
  reviewer did not consider it one). GameGate holds/prioritizes messages on *its
  own* surface and gives a recap; it does not reach into Gmail/Discord to
  suppress *their* native notifications. The honest positioning is "mute your
  native alerts; GameGate becomes your one game-aware surface." Whether/how to
  reword the pitch is the owner's decision — asked.

**Still deferred (need the owner, Windows, or a dedicated PR):**
| Item | Why | Needs |
|---|---|---|
| ~~Dashboard-DND vs detector state machine~~ | **DONE (owner chose: dashboard wins), in two parts.** DND is a persisted `override_state` distinct from the detector's `state`; `POST /status/dnd` sets it, a detector `POST /status` can't clear it or drive sessions while held, and `GET /status` reports the override as the effective state with a `manual_override` flag. **Correction (round 10):** the first cut claimed "off hands control back on the next poll" — that was wrong, because the real detector only POSTs on a *transition*, so a game still running across the DND window never re-announces itself and the post-DND stretch was silently lost. `set_dnd(False)` now re-opens the session itself when the base state is still `gaming`, and a regression test proves it WITHOUT a detector re-post. The tray app's own DND button was also migrated onto `/status/dnd`. | — |
| Tkinter single-UI-thread refactor | Windows-only, not CI-testable | Windows pass |
| Signed/hash-verified release updater | Source updater is a dev convenience today | Release-signing work |
| Dependency lockfile + SHA-pinned actions | Changes the CI pipeline | Dedicated CI PR (gitleaks download already checksum-pinned) |
| CSP `unsafe-inline` removal | ~20 inline handlers → addEventListener | Dashboard PR |
| Connector heartbeats (live health) | New persisted state + UI | Feature PR (limitation now documented) |
| One-time dashboard login ticket | Removes the token from the `?key=` URL | Auth change (caveat documented) |
| Poison-item server-side dead-letter | 200 given-up items could block newer pending | Server + desktop change |

## Review round 10 — Claude critic + OpenAI white-hat pentest — 2026-08-24

A fresh Claude senior-engineer critic (pointed at the newest DND/ticket/dead-
letter/heartbeat code) and an OpenAI agent running an adversarial white-hat
pentest. **Pentest verdict: no critical exploit, no unauthenticated remote
takeover, and no SQLi / SSRF / path-traversal / cookie-forgery / OAuth-takeover.**
The Claude critic returned 0 blockers, 1 major, 3 minor, 5 nitpick.

**Fixed this round:**
- **DND-off recap loss (MAJOR — and a false "DONE" I had to walk back).** The
  first DND cut claimed "off hands control back on the next poll", but the real
  detector only POSTs on a *transition*, so a game still running across the DND
  window never re-announced itself and the whole post-DND stretch was silently
  un-recapped. `set_dnd(False)` now re-opens the session itself from the stored
  base state; a regression test proves it WITHOUT a detector re-post (the old
  test masked the bug by manually re-POSTing). Removed the dead `base_state()`
  helper that was written for this and never wired up.
- **Tray DND unified onto `/status/dnd`** — the button users actually click now
  drives the same server-side override as the dashboard (no more posting
  focused/available as a base state, no detector-pausing hack).
- **Heartbeat can now detect a *silent* death** (crash/OOM), not just an error:
  a connector that stops beating for >5 min shows `degraded/stale`; added a
  periodic Discord heartbeat so its freshness is meaningful.
- **Pentest fixes:** `/ready` returns a fixed public body (no raw DB exception
  text); `?ticket=` guesses now count toward the auth throttle (the keyless-
  login exemption no longer covers them); the heartbeat endpoint rejects unknown
  connector names; the dashboard session TTL dropped 90d → 14d; `/docs` is now
  gated on *explicit* dev (a bare production run no longer exposes the inventory);
  **PKCE (S256)** added to the Gmail OAuth flow; extra systemd hardening on all
  three units (`PrivateDevices`, `ProtectKernelModules/Logs`, `ProtectClock`,
  `LockPersonality`, `RestrictNamespaces/Realtime`, `RestrictAddressFamilies`,
  `SystemCallArchitectures=native`).
- **Nitpicks:** `/data/clear` now also resets `override_state`; docs "7 → 8
  persisted stores"; ARCHITECTURE Slack "v0.1" label; classifier default model
  id set to a real model (`gpt-4o-mini`). The pentest's "missing .gitignore" was
  a bundler false-positive — `.gitignore` exists and `git check-ignore` confirms
  `.env`, `token.json`, and `agent/config.json` are all ignored.

**Accepted / deferred with reasons (documented single-user tradeoffs, not bugs):**
| Pentest finding | Disposition |
|---|---|
| HIGH: one shared, unscoped master token (a local foothold = owner access) | Accepted single-user tradeoff (SECURITY_DISPOSITIONS #27/#45). Per-component OAuth-style scopes are a real feature, unwarranted while every component runs as one user on one host; revisit at multi-user/multi-host. |
| MEDIUM: stateless 90-day cookie, logout doesn't revoke | Partly fixed (TTL 90d→14d; token rotation already invalidates all cookies). Full server-side revocable sessions are a follow-up. |
| MEDIUM: `?key=` still supported + desktop fallback | The desktop already *prefers* the one-time ticket; `?key=` remains for the shareable DM link (single-user, HTTPS, logs scrubbed). Removing it entirely is a follow-up. |
| HIGH: unsigned `git pull` updater / unpinned release deps | Needs a code-signing cert and a Windows-verified lockfile; documented in ROADMAP. CI actions are SHA-pinned and the gitleaks download is checksum-verified. |
| MEDIUM: services share a user/.env; ReadWritePaths = whole checkout | Added the safe extra sandboxing above. Separate users + narrowed writable paths need relocating writable state (`.env` lock/tmp, `token.json`, DB) into a dedicated dir — an ops change to verify live; documented follow-up. |
| MEDIUM: no CSRF token; SameSite=lax only | The pentester did NOT confirm conventional CSRF (SameSite=lax blocks the cross-site POST) for this single-host deployment; an Origin/CSRF token is defense-in-depth for a future shared-domain deployment. |
