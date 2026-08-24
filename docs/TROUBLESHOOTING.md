# Troubleshooting

State the failure as "expected X, observed Y", find the layer, test the layer underneath directly, change one thing.

| Symptom | First check |
|---------|-------------|
| API won't start | Read the traceback. Wrong venv? `which python`. Port taken? `ss -ltnp \| grep 8000` |
| `/health` works locally, fails through Nginx | `sudo nginx -t`, then `/var/log/nginx/error.log`. 502 = Nginx can't reach Uvicorn → is the service running? |
| Detector "does nothing" | Run `python detector.py --once` and read the log lines. Is the game's real process name in config.json? (Task Manager → Details) |
| Detector logs "API unreachable" | `curl https://<your-host>/health` from the gaming PC (the API is HTTPS via nginx; uvicorn is 127.0.0.1-only). A wrong token now logs "token rejected (401)", not "unreachable" |
| Events duplicated | They can't be, by design — check you're not generating different external_ids for the same message |
| Digest never arrives | Was a session actually closed? `journalctl -u gamegate \| grep "session closed"`. Is the Discord connector running and is GAMEGATE_DISCORD_CHANNEL_ID right? |
| Digest arrives twice | Delivery is at-least-once (send-then-ack): a send that succeeds while its ack fails re-sends. A digest is built at most once (DB UNIQUE(session_id)); a duplicate *delivery* is possible. If persistent: two connector processes — `ps aux \| grep discord_bot` |
| Gmail keeps re-ingesting | It re-polls the same messages by design; the API dedupes. Storage growing = check external_ids are stable |
| Slack silent | Socket Mode enabled? Both tokens set and not swapped? Bot invited to the channel? Event subscription `app_mention` added? |
| Classifier "weird" | It can't break routing (fallback). Check logs for "fell back to deterministic". Disable with CLASSIFIER_ENABLED=false |
| 401 on writes | Client missing `X-GameGate-Token` header or value differs from server `.env` |
| Tests pass locally, fail in CI | Version/dependency drift — compare CI's install step with your venv |

## Log locations

- API + connectors (systemd): `journalctl -u gamegate -f`, `journalctl -u gamegate-discord -f`
- Nginx: `/var/log/nginx/gamegate.access.log` (query strings stripped), `/var/log/nginx/error.log`
- Detector: stdout (add `> detector.log 2>&1` or Task Scheduler logging)

Logs never contain secrets or full message bodies — grep for `state transition`, `session`, `fell back`, `unreachable` as anchors.


## Field-verified issues (found and fixed during live testing, 2026-08-23)

These five really happened on the first live night — kept here because they're the
most likely drill questions:

1. **Ctrl+C didn't stop the tray app** → pystray swallows SIGINT. Fixed: signal
   handler + tray Quit. If an old build lingers: Task Manager → end Python/GameGate.
2. **"Quit" seemed to do nothing** → multiple app instances + Windows ghost tray
   icons. Fixed: single-instance lock (second launch exits with "already running").
   Ghost icons vanish when you hover over them.
3. **State stuck on GAMING with no game running** → Wallpaper Engine lives in
   steamapps/common and runs 24/7; path-based detection flagged it. Fixed: helper
   exclusion list + `ignore_processes` in config.json. Diagnose with: `SELECT
   application FROM sessions ORDER BY started_at DESC LIMIT 1`.
4. **Pinged by your own messages** → the Discord connector ingested the owner's
   messages; urgent keywords broke through. Fixed: `GAMEGATE_OWNER_DISCORD_ID`
   skip.
5. **Overlay text clipped / countdown bar through the text** → DPI-aware fonts
   scale with Windows display scaling but layout was raw pixels. Fixed: one DPI
   factor drives all metrics and font sizes.
