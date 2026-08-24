# GameGate desktop app (Windows)

The tray app is THE notification surface: urgent break-throughs and post-game digests appear as an **overlay box in the top-right of the screen (~15% tall), with a sound** — always on top of the game, never stealing focus, auto-dismissing after 8s (click to dismiss early). It also runs the game detector, so on the PC you start ONE thing.

Why an overlay instead of native Windows toasts: Focus Assist silences toasts during fullscreen gaming — exactly when the break-through matters. The overlay isn't subject to it. (Native toasts remain available via `"notifier": "toast"` in config.json. Fullscreen-EXCLUSIVE games hide any overlay — Discord's too; use borderless/windowed, the modern default.)

## Run from source (first test)

```powershell
py -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install psutil pystray pillow pywebview   # (+ winotify only for toast mode)
cd agent
copy config.example.json config.json    # set api_url + api_token (games auto-detect)
python tray_app.py
```

A colored dot appears in the tray: ⚪ available (neutral gray) · 🟢 gaming (green — the gate is up) · 🔴 do-not-disturb / focused (red).
Right-click → **Open GameGate** (the desktop window: Inbox / Connections / Settings) / Status / Last digest / Do Not Disturb / Quit.

The window uses Windows' built-in Edge WebView2 engine (preinstalled on Windows 11). It keeps the NATIVE window frame — so Snap, Win+arrow, and maximize all work — with the title bar painted in app colors via DWM.

## Package into GameGate.exe

```powershell
pip install pyinstaller
cd agent
python -m PyInstaller --onefile --noconsole --name GameGate --icon gamegate.ico tray_app.py
# → dist\GameGate.exe
```

Put `config.json` NEXT TO the exe (secrets are read at runtime, never bundled).
Desktop shortcut: right-click `GameGate.exe` → Send to → Desktop.

## Start with Windows

Win+R → `shell:startup` → drop a shortcut to GameGate.exe there. Done.

## Reliability contract

Toasts are acked only after they actually show, so nothing is lost — if the app
is closed or a toast fails, items stay queued on the server and arrive when the
app is back. Delivery is **at-least-once**: the pump shows-then-acks, so a show
that succeeds while its ack fails can re-show. Within one app run each item is
rendered at most once (tracked in memory); across a restart, a rare duplicate is
possible. An item that fails to show/ack repeatedly is dropped from the client
after a few attempts (it stays on the server) rather than looping forever.
