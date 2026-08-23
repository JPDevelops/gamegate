# Slack setup (needs a workspace you're allowed to add apps to)

Socket Mode = no public URL needed; the connection is outbound from GameGate.

1. https://api.slack.com/apps → Create New App → From scratch → pick your **test** workspace.
2. **Socket Mode** → enable → generate an **app-level token** with `connections:write` (starts `xapp-`).
3. **OAuth & Permissions** → Bot Token Scopes: `app_mentions:read`, `chat:write` (minimum only) → Install to Workspace → copy the **bot token** (starts `xoxb-`).
4. **Event Subscriptions** → enable → Subscribe to bot events: `app_mention`.
5. In `.env`:
   ```
   SLACK_ENABLED=true
   SLACK_BOT_TOKEN=xoxb-...
   SLACK_APP_TOKEN=xapp-...
   ```
6. Invite the bot to a channel and mention it. The mention lands in GameGate as an `actionable` event ("urgent"/"asap" in the text upgrades it to urgent).

Two different tokens is the classic trap: `xoxb-` authenticates API calls, `xapp-` authenticates the Socket Mode connection. Both stay in `.env`, never in git.
