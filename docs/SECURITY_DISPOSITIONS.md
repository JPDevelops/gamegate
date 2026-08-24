# Security dispositions — v0.1 (lab deployment)

Runbook Step 14 asks every deployment choice to be deliberate. These are the open
security items, each with an explicit decision for the v0.1 lab context (one user,
one server, LAN + one WAN port) and its revisit trigger.

| # | Item | v0.1 decision | Rationale | Revisit when |
|---|------|---------------|-----------|--------------|
| #26 | TLS at Nginx | **Done — TLS live on 443** | Let's Encrypt cert via sslip.io; the HTTPS endpoint is what the dashboard and clients use (`nginx/gamegate-tls.conf`). Port 80 is kept for existing plain-HTTP lab clients on the LAN; migrating those off HTTP is the remaining sub-item. | Retire port 80 once all clients use HTTPS. |
| #27 | Scoped per-component tokens | **Accepted risk for lab** | All clients are owned by the same person on the same trust level today; compromise of any device already means owner-level compromise. | First moment two components have different trust levels (e.g. SaaS, shared server, or a connector on third-party infra). |
| #45 | Per-service env files | **Accepted risk for lab** | All services run as the same user on the same host; splitting env files adds ops friction without a trust boundary to enforce. | When services get separated (containers, multiple hosts, or any untrusted code in one service). |
| #28 | Transactional session close | **Open engineering debt, scheduled** | Not a security item; data-integrity on crash timing. Invisible in normal use. | Next hardening sprint (before v0.2 ships). |

Standing controls already in force: the app refuses to start without a token unless
GAMEGATE_ENV=development is set explicitly (an unset token fails closed, not open);
`.env` is loaded via python-dotenv so the documented setup is actually authenticated;
all data endpoints authenticated; the dashboard uses a signed session cookie, never
the raw token; security headers wrap even rate-limited 429s; least-privilege API
scopes per integration; secrets gitignored + full-history scan clean; UMask=0077
units; owner-only bot commands; fail-closed Discord config; logs carry no bodies or
secrets.


## Branch protection (added 2026-08-24)

**Enforced.** The repo is public (branch protection is free on public repos),
and `main` is protected: the `test` status check must pass and changes must go
through a PR before merge, in strict mode (branch must be current). Verified by
a demo PR that was blocked while its CI was red and unblocked once green.
Remaining hardening: `enforce_admins` is currently off, so the owner can
override in an emergency; turn it on to make the rule apply to every merge with
no exception.

## Pre-launch checklist audit (2026-08-24, 20-item)

| # | Item | Status |
|---|------|--------|
| 1 | Hide API keys | ✅ .env only, gitignored, never in history |
| 2 | Purge git secrets | ✅ full-history all-branch scan clean |
| 3 | Use public DB key | N/A — SQLite, no cloud DB keys |
| 4 | Row-level security | N/A now (single-user); = multi-tenant isolation at v1.0 |
| 5 | Encrypt sensitive data | OAuth token stored 0600; data-at-rest = host disk (documented) |
| 6 | Server-side auth | ✅ require_api_token on all data/write endpoints |
| 7 | Lock record access | N/A now (single-user); v1.0 multi-tenant |
| 8 | Block field tampering | ✅ Pydantic models whitelist + type-validate |
| 9 | Secure session cookies | ✅ HttpOnly + SameSite=lax + Secure over HTTPS; cookie is a signed, expiring session token (HMAC of the master secret), never the raw token — a cookie leak is time-boxed and not the master credential; /logout clears it |
| 10 | Hash passwords | N/A — token auth, no passwords |
| 11 | Rate limit login | ✅ AuthRateLimitMiddleware throttles repeated 401s per IP |
| 12 | Bot protection | Unauthenticated routes are limited to /health, the OAuth callback (state-protected), /logout, and /docs+/openapi (development only); every data/write route requires the token. No public forms |
| 13 | Parameterize queries | ✅ all SQL uses ? placeholders (only fixed-table-name DELETE excepted) |
| 14 | Validate all input | ✅ Pydantic on every request body; enums 422 bad values |
| 15 | Escape user content | ⚠️ Partial. esc() (escapes & < > " ') covers dashboard TEXT sinks; attacker-controlled fields (sender/title) land in text context and are escaped. BUT esc() is HTML-entity escaping and the template also interpolates ids into inline `onclick="…'${esc(id)}'…"` handlers — the wrong escaper for a JS-string sink. Not exploitable today only because those ids are server-generated uuids, not user input. Real fix is tracked (M24: drop CSP `unsafe-inline`, move handlers to addEventListener). Do NOT add a user-controlled value to an inline handler until then. Overlay is tkinter (no HTML sink). |
| 16 | Restrict file uploads | N/A — no uploads |
| 17 | Trim API responses | Partial: /events, /status, /health, classify use a typed response_model; the dashboard/settings/connector routes return plain dicts assembled server-side (no model leakage, but not schema-typed) |
| 18 | Security headers | ✅ SecurityHeadersMiddleware (nosniff, DENY, CSP, Referrer, Permissions) |
| 19 | Force HTTPS | HSTS sent over HTTPS; TLS live on 443; port 80 kept for local clients (documented) |
| 20 | Scan dependencies | ✅ Dependabot alerts + security fixes + weekly PRs |
