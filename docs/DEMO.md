# Demo script (5 minutes, tells the story end to end)

**Setup beforehand:** API running behind nginx; the **GameGate desktop app** (tray notifier) running on the gaming PC with a real game configured; one terminal on the server; the GitHub repo open in a browser tab. (The desktop app is the default notifier — Discord delivery is opt-in via `GAMEGATE_DISCORD_DELIVERY=true`; this script shows the desktop overlay.)

1. **The repo is the product** (30s): show Issues (closed, with acceptance criteria), a merged PR with its diff, and a green Actions run. "Every change went issue → branch → tested PR → CI → merge."
2. **The service is alive** (20s): `curl https://<your-host>/health` — status + version.
3. **Automatic detection** (60s): launch the game on the PC. Show `journalctl -u gamegate -f` printing `State transition: available -> gaming` with no human input. The dashboard shows "🎮 Gaming — non-urgent messages are being held."
4. **Suppression while gaming** (60s): inject a normal event (or have someone message). The dashboard's Messages tab shows it queued. Nothing pinged you.
5. **Break-through** (30s): inject an urgent event → the desktop overlay pops top-right immediately, despite gaming.
6. **The digest** (60s): quit the game. Detector reports the transition; ONE post-game recap overlay/dashboard entry appears: duration, counts, action-required items.
7. **AI with a kill switch** (60s): show a classified event; then set an invalid OPENAI_API_KEY, classify again — deterministic fallback answers, service unbothered. "The LLM is an enhancement, never a dependency."
8. **Prove the tests** (20s): `pytest -q` → full offline suite passing (160+).

Inject a test event manually. **`received_at` must be NOW** — a fixed past
timestamp is treated as stale (older than the 10-minute freshness window) and
gets queued instead of breaking through, so the overlay never pops:
```bash
curl -X POST https://<your-host>/events -H "Content-Type: application/json" \
  -H "X-GameGate-Token: $TOKEN" -d "{
  \"source\":\"system\",\"external_id\":\"demo-$(date +%s)\",\"sender\":\"demo\",
  \"title\":\"Urgent: call your mother\",\"content\":\"now\",
  \"received_at\":\"$(date -u +%FT%TZ)\",\"priority\":\"urgent\"}"
```
