# Security dispositions — v0.1 (lab deployment)

Runbook Step 14 asks every deployment choice to be deliberate. These are the open
security items, each with an explicit decision for the v0.1 lab context (one user,
one server, LAN + one WAN port) and its revisit trigger.

| # | Item | v0.1 decision | Rationale | Revisit when |
|---|------|---------------|-----------|--------------|
| #26 | TLS between agent/connectors and Nginx | **Accepted risk for lab** | Traffic crosses the internet HTTP; token could be sniffed on-path. Mitigations in place: token is random 48-hex, rotatable in seconds, grants access to one person's notification metadata only; no message bodies beyond safe snippets. | Before ANY second user, before the repo/site goes public, or with v0.2 dashboard (which carries session auth) — then TLS at Nginx via Let's Encrypt or a VPN. |
| #27 | Scoped per-component tokens | **Accepted risk for lab** | All clients are owned by the same person on the same trust level today; compromise of any device already means owner-level compromise. | First moment two components have different trust levels (e.g. SaaS, shared server, or a connector on third-party infra). |
| #45 | Per-service env files | **Accepted risk for lab** | All services run as the same user on the same host; splitting env files adds ops friction without a trust boundary to enforce. | When services get separated (containers, multiple hosts, or any untrusted code in one service). |
| #28 | Transactional session close | **Open engineering debt, scheduled** | Not a security item; data-integrity on crash timing. Invisible in normal use. | Next hardening sprint (before v0.2 ships). |

Standing controls already in force: production refuses to start without a token; all
data endpoints authenticated; least-privilege API scopes per integration; secrets
gitignored + full-history scan clean; UMask=0077 units; owner-only bot commands;
fail-closed Discord config; logs carry no bodies or secrets.


## Branch protection (added 2026-08-24)

**Requested by the Product Owner; configured intent documented, not enforced.**
GitHub gates branch protection behind Pro or a public repo; this repo is
private + free. Intended rule for `main` when the repo goes public or Pro:
require the `test` status check to pass and a PR before merge (no direct
pushes). Command ready: `gh api -X PUT repos/JPDevelops/gamegate/branches/main/protection ...`
with required_status_checks.contexts=["test"].
