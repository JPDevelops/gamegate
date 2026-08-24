# GameGate Roadmap — from "works for me" to "universal"

## Where we are: v0.1 (personal, self-hosted) — DONE
A complete, tested, live product for ONE user (Jules) on ONE server. Every
runbook milestone met and exceeded (desktop app, dashboard, interactive
connectors, settings, self-updater). This is the version the interview reviews.

## What "universal" actually means — and what's already built

| "Universal" piece | Status | What's left for public/universal |
|-------------------|--------|----------------------------------|
| **Website** | BUILT (parked on gh-pages) | One "go public" flip — then live at jpdevelops.github.io/gamegate |
| **Desktop .exe** | BUILT + self-updating | Public download tier: CI-built signed exe on Releases (issue #72); code-signing cert removes the SmartScreen warning |
| **Easy connectors** | Interactive Connectors tab + Add-a-connector catalog EXIST | Per-app OAuth flows behind the "Connect" buttons (Gmail flow already built as the template); Windows notification-listener for catch-all (issue #48) |

So "universal" is less about new features than about **who can use it**.

## The one real gate: single-user → multi-user

Everything today assumes one person, one server, one token. "Universal"
(strangers can sign up and use it) requires:

1. **Multi-tenancy** — accounts, login, per-user data isolation, per-user
   connectors. This is the runbook's own interview question #17.
2. **Google verification** — Gmail's restricted scope needs a paid annual
   security assessment before >100 public users (documented in GMAIL_SETUP).
3. **Hosting + ops** — a real domain, TLS at scale, backups, monitoring,
   someone on call. GameGate stops being a project and becomes a service.
4. **Legal** — a privacy policy that means it, because you'd hold strangers'
   message data.

## Sequenced plan

- **v0.1 (now):** freeze for the review. Pass the interview — that's the gate
  that legitimizes and funds everything below.
- **v0.2 (personal, better):** the dashboard's remaining connector flows,
  Windows notification listener (#48), auto-AWAY (#56), the deferred
  hardening (#25, #28). Still single-user; all reusable in SaaS.
- **v1.0 (universal/SaaS):** multi-tenancy, Google verification, hosted
  infra, billing, legal. A funded venture with an owner — not a weekend.

## The honest framing for the review
"I built a complete personal product and I know exactly what turning it into
a public service costs — here's the roadmap, the tenancy model, the
compliance path, and the cost drivers." A working v0.1 plus this plan beats a
half-built SaaS every time.
