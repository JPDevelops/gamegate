# GameGate Roadmap — from "works for me" to "universal"

## Where we are: v0.2 (personal, self-hosted) — DONE
A complete, tested, live product for ONE user on ONE server. Every
milestone met and exceeded (desktop app, dashboard, interactive
connectors, settings, source-install self-updater).

## What "universal" actually means — and what's already built

| "Universal" piece | Status | What's left for public/universal |
|-------------------|--------|----------------------------------|
| **Website** | BUILT (parked on gh-pages) | One "go public" flip — then live at jpdevelops.github.io/gamegate |
| **Desktop .exe** | BUILT; self-update works for a source (git) install | Public download tier: a downloaded release exe has no git checkout, so the current `git pull` + rebuild updater doesn't apply to it — the real path is a CI-built **signed** exe on Releases that verifies a release hash/signature before replacing itself (issue #72); code-signing cert also removes the SmartScreen warning |
| **Easy connectors** | Interactive Connectors tab + Add-a-connector catalog EXIST | Per-app OAuth flows behind the "Connect" buttons (Gmail flow already built as the template); Windows notification-listener catch-all — DONE (opt-in, reads all OS notifications) |

So "universal" is less about new features than about **who can use it**.

## The one real gate: single-user → multi-user

Everything today assumes one person, one server, one token. "Universal"
(strangers can sign up and use it) requires:

1. **Multi-tenancy** — accounts, login, per-user data isolation, per-user
   connectors.
2. **Google verification** — Gmail's restricted scope needs a paid annual
   security assessment before >100 public users (documented in GMAIL_SETUP).
3. **Hosting + ops** — a real domain, TLS at scale, backups, monitoring,
   someone on call. GameGate stops being a project and becomes a service.
4. **Legal** — a privacy policy that means it, because you'd hold strangers'
   message data.

## Sequenced plan

- **v0.2 (now):** the personal product, hardened — signed session cookie,
  transactional session close (#28, done), the interactive Connectors tab, and
  the recap correctness work. Still single-user; all reusable in SaaS.
- **v0.3 (personal, better):** the dashboard's remaining connector flows,
  Windows notification listener (#48), auto-AWAY (#56), the remaining deferred
  hardening (#25). Still single-user.
- **v1.0 (universal/SaaS):** multi-tenancy, Google verification, hosted
  infra, billing, legal. A funded venture with an owner — not a weekend.

## The honest framing
"I built a complete personal product and I know exactly what turning it into
a public service costs — here's the roadmap, the tenancy model, the
compliance path, and the cost drivers." A working personal product plus this
plan beats a half-built SaaS every time.

## Cost to go universal (2026 estimate)

**Tier 1 — public beta (~<$20/month):** small cloud VM for the shared server
($6-12/mo), domain (~$12/yr), Let's Encrypt TLS (free), GitHub Pages (free).
Viable while under Google's 100-test-user cap with the "unverified app"
warning shown.

**Tier 2 — real public product (~$1-5K/year):**
- **Google OAuth verification (CASA security assessment): ~$500-4,000/yr** —
  the dominant cost; required for Gmail restricted scope beyond 100 users
  without the warning.
- Code-signing certificate: ~$100-400/yr (removes the Windows unknown-
  publisher warning on the exe).
- Hosting at scale: ~$20-100/mo early.
- Email/monitoring/backups: ~$0-30/mo on free tiers.

Bottom line: pocket money to pilot with real people; a low-four-figure annual
commitment (Google verification dominated) to go fully public. This is a
business decision, not an engineering one.
