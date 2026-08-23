# Deployment — Linux server, layer by layer

Follow the ladder; verify each layer before adding the next. Never debug two layers at once.

## Layer 1 — Uvicorn manually

```bash
cd ~/gamegate && source .venv/bin/activate
cp .env.example .env   # set GAMEGATE_API_TOKEN at minimum
uvicorn app.main:app --host 127.0.0.1 --port 8000
# verify (second terminal):
curl http://127.0.0.1:8000/health     # → {"status":"ok","version":"0.1.0"}
```

## Layer 2 — systemd service

```bash
sudo cp deploy/gamegate.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now gamegate
# verify:
systemctl status gamegate
journalctl -u gamegate -n 50 --no-pager
curl http://127.0.0.1:8000/health
```

Same for the Discord connector: `deploy/gamegate-discord.service`.

## Layer 3 — Nginx

```bash
sudo apt install nginx
sudo cp nginx/gamegate.conf /etc/nginx/sites-available/gamegate
sudo ln -s /etc/nginx/sites-available/gamegate /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default   # if present
sudo nginx -t                              # ALWAYS test config first
sudo systemctl reload nginx
# verify:
curl http://<server-lan-ip>/health         # through Nginx this time
```

If Layer 3 fails but Layer 1's curl works, the problem is Nginx config — check `/var/log/nginx/error.log`. If both fail, the problem is the app — check `journalctl -u gamegate`.

## Layer 4 — public exposure (ONLY if actually needed)

Not required for GameGate to function (all integrations are outbound). If ever needed: DNS record → TLS via certbot (`listen 443 ssl`) → firewall allowing 80/443 only. Uvicorn stays on 127.0.0.1 forever.

## Detector on the gaming PC

```powershell
# Windows PowerShell
py -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install psutil
cd agent ; copy config.example.json config.json   # edit: server URL, token, game list
python detector.py
```
Auto-start later via Task Scheduler (run at logon, "start in" = agent folder).
