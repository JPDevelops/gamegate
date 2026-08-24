# Gmail setup (one-time, ~5 minutes, needs a browser)

GameGate uses the official Gmail API, **read-only scope**, via OAuth. Nothing is sent or deleted, ever.

1. Go to https://console.cloud.google.com → create project `gamegate`.
2. APIs & Services → Library → enable **Gmail API**.
3. APIs & Services → OAuth consent screen → External → add your own Gmail as a **test user** (no verification needed for testing).
4. Credentials → Create credentials → **OAuth client ID** → **Web application**. Add an **Authorized redirect URI** of `https://<YOUR-HOST>.sslip.io/oauth/gmail/callback` (this must match `GMAIL_REDIRECT_URI`). The in-app flow is a web redirect flow, so a *Desktop*-type client will be rejected.
5. Put the client id/secret in the server `.env`:
   ```
   GMAIL_OAUTH_CLIENT_ID=...
   GMAIL_OAUTH_CLIENT_SECRET=...
   GMAIL_REDIRECT_URI=https://<YOUR-HOST>.sslip.io/oauth/gmail/callback
   ```
6. **Preferred:** open the dashboard → Connectors → **Connect Gmail**. That runs the OAuth flow and writes `token.json` server-side for you — no manual scp. Then flip it on:
   ```
   GMAIL_ENABLED=true
   GMAIL_TOKEN_PATH=/home/ubuntu/Project/gamegate/token.json   # inside the app dir (writable)
   ```
   (VIP senders are configured in the dashboard Settings, stored in the DB — there is no `GMAIL_VIP_SENDERS` env var.)

**Never commit** `credentials.json` or `token.json` (both gitignored). If either ever lands in git history: revoke in Google console and rotate — deleting the file is not enough.
