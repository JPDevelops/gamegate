# Gmail setup (one-time, ~5 minutes, needs a browser)

GameGate uses the official Gmail API, **read-only scope**, via OAuth. Nothing is sent or deleted, ever.

1. Go to https://console.cloud.google.com → create project `gamegate`.
2. APIs & Services → Library → enable **Gmail API**.
3. APIs & Services → OAuth consent screen → External → add your own Gmail as a **test user** (no verification needed for testing).
4. Credentials → Create credentials → **OAuth client ID** → Desktop app → download the JSON as `credentials.json`.
5. On a machine with a browser, in the repo venv:
   ```bash
   pip install -e ".[gmail]"
   python -c "
   from google_auth_oauthlib.flow import InstalledAppFlow
   flow = InstalledAppFlow.from_client_secrets_file('credentials.json',
       ['https://www.googleapis.com/auth/gmail.readonly'])
   creds = flow.run_local_server(port=0)
   open('token.json','w').write(creds.to_json())
   print('token.json written')"
   ```
6. Move `token.json` to the server (e.g. `scp`), `chmod 600 token.json`, set in `.env`:
   ```
   GMAIL_ENABLED=true
   GMAIL_TOKEN_PATH=/home/ubuntu/gamegate/token.json
   GMAIL_VIP_SENDERS=dad@example.com,boss@example.com
   ```

**Never commit** `credentials.json` or `token.json` (both gitignored). If either ever lands in git history: revoke in Google console and rotate — deleting the file is not enough.
