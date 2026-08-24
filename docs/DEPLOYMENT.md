# Deployment — Linux server, layer by layer

Follow the ladder; verify each layer before adding the next. Never debug two layers at once.

## Layer 1 — Uvicorn manually

```bash
cd /home/ubuntu/Project/gamegate && source .venv/bin/activate   # match the systemd units' path
cp .env.example .env   # set GAMEGATE_API_TOKEN at minimum
uvicorn app.main:app --host 127.0.0.1 --port 8000
# verify (second terminal):
curl http://127.0.0.1:8000/health     # → {"status":"ok","version":"0.2.0"}
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

## Layer 3 — Nginx + TLS

`nginx/gamegate.conf` (port 80) is **redirect-only** — it 301s everything to
HTTPS. The real API is served by `nginx/gamegate-tls.conf` (port 443). Both
reference the `gamegate_noargs` log format, which is defined once in
`nginx/gamegate-log.conf` and MUST be installed to `conf.d/` or `nginx -t`
fails with `unknown log format`.

```bash
sudo apt install nginx certbot python3-certbot-nginx
# log format (shared by both vhosts) — install this FIRST
sudo cp nginx/gamegate-log.conf /etc/nginx/conf.d/gamegate-log.conf
# :80 redirect vhost
sudo cp nginx/gamegate.conf /etc/nginx/sites-available/gamegate
sudo ln -s /etc/nginx/sites-available/gamegate /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
# TLS: get a cert for your hostname, then install the :443 vhost.
# (sslip.io gives you a free hostname from your IP, e.g. <IP>.sslip.io.)
sudo certbot certonly --nginx -d <YOUR-HOST>.sslip.io
sudo sed "s/YOUR-HOST/<YOUR-HOST>/g" nginx/gamegate-tls.conf \
  | sudo tee /etc/nginx/sites-available/gamegate-tls
sudo ln -s /etc/nginx/sites-available/gamegate-tls /etc/nginx/sites-enabled/
sudo nginx -t                              # ALWAYS test config first
sudo systemctl reload nginx
# verify (HTTPS, since :80 only redirects):
curl https://<YOUR-HOST>.sslip.io/health   # → {"status":"ok",...}
curl -I http://<YOUR-HOST>.sslip.io/health # → 301 to https
```

If Layer 3 fails but Layer 1's curl works, the problem is Nginx config — check
`/var/log/nginx/error.log`. If both fail, the problem is the app — check
`journalctl -u gamegate`.

Uvicorn stays bound to `127.0.0.1` forever; nginx is the only thing on the
network. Point the detector/connectors at the `https://` host.

## Detector on the gaming PC

```powershell
# Windows PowerShell
py -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install psutil
cd agent ; copy config.example.json config.json   # edit: server URL, token, game list
python detector.py
```
Auto-start later via Task Scheduler (run at logon, "start in" = agent folder).
