# Demo script (5 minutes, tells the story end to end)

**Setup beforehand:** API + Discord connector running; detector on the gaming PC with a real game configured; one terminal on the server; the GitHub repo open in a browser tab.

1. **The repo is the product** (30s): show Issues (closed, with acceptance criteria), a merged PR with its diff, and a green Actions run. "Every change went issue → branch → tested PR → CI → merge."
2. **The service is alive** (20s): `curl localhost:8000/health` — status + version.
3. **Automatic detection** (60s): launch the game on the PC. Show `journalctl -u gamegate -f` printing `State transition: available -> gaming` with no human input. `!status` in Discord → "🎮 Gaming — non-urgent messages are being held."
4. **Suppression while gaming** (60s): inject a normal event (or have someone message). `!digest` shows it queued. Nothing pinged you.
5. **Break-through** (30s): inject an urgent event → it posts to Discord immediately despite gaming.
6. **The digest** (60s): quit the game. Detector reports the transition; ONE digest appears in Discord: duration, counts, action-required items.
7. **AI with a kill switch** (60s): show a classified event; then set an invalid OPENAI_API_KEY, classify again — deterministic fallback answers, service unbothered. "The LLM is an enhancement, never a dependency."
8. **Prove the tests** (20s): `pytest -q` → 74+ passing, offline.

Inject a test event manually:
```bash
curl -X POST localhost:8000/events -H "Content-Type: application/json" \
  -H "X-GameGate-Token: $TOKEN" -d '{
  "source":"system","external_id":"demo-1","sender":"demo",
  "title":"Urgent: call your mother","content":"now",
  "received_at":"2026-08-23T12:00:00Z","priority":"urgent"}'
```
