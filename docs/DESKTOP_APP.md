# GameGate desktop app (Windows)

The tray app is THE notification surface: urgent break-throughs and post-game digests arrive as native Windows toasts. It also runs the game detector, so on the PC you start ONE thing.

## Run from source (first test)

```powershell
py -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install psutil pystray pillow winotify
cd agent
copy config.example.json config.json    # set api_url + api_token (games auto-detect)
python tray_app.py
```

A colored dot appears in the tray: 🟢 available · 🟣 gaming · 🔴 do-not-disturb.
Right-click → Status / Last digest / Do Not Disturb / Quit.

## Package into GameGate.exe

```powershell
pip install pyinstaller
cd agent
pyinstaller --onefile --noconsole --name GameGate tray_app.py
# → dist\GameGate.exe
```

Put `config.json` NEXT TO the exe (secrets are read at runtime, never bundled).
Desktop shortcut: right-click `GameGate.exe` → Send to → Desktop.

## Start with Windows

Win+R → `shell:startup` → drop a shortcut to GameGate.exe there. Done.

## Reliability contract

Toasts are acked only after they actually show. App closed or toast failed →
items stay queued on the server and arrive when the app is back. Nothing is
lost; nothing shows twice.
