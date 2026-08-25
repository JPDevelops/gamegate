# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the GameGate desktop app.

This bundles BOTH the desktop tray/agent (this folder) AND the full FastAPI
server (../app) into one onefile GameGate.exe, so the app can run the server
locally on 127.0.0.1 with no cloud and no configuration (local mode).

Build from this folder:  pyinstaller GameGate.spec
Output:                  dist\\GameGate.exe

Why a .spec instead of the old one-liner: we need to (a) put the repo root on
the path so `import app...` resolves, (b) ship app/templates/dashboard.html as
data, and (c) pull in uvicorn's dynamically-imported submodules. A flag-only
command can't express that.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

# Repo root (one level up from this agent/ folder) so `import app` works both
# for this spec's collect_submodules() call and inside the frozen app.
REPO_ROOT = os.path.abspath(os.path.join(os.getcwd(), ".."))
sys.path.insert(0, REPO_ROOT)

hiddenimports = []
hiddenimports += collect_submodules("app")        # the FastAPI server package
hiddenimports += collect_submodules("uvicorn")    # dynamically-imported loops/protocols
hiddenimports += ["dotenv", "h11", "anyio"]

# Notification capture reads the Windows notification database directly (see
# notif_db.py) — no winsdk / packaged-app API needed, so nothing extra to bundle.

a = Analysis(
    ["tray_app.py"],
    pathex=[REPO_ROOT],
    binaries=[],
    datas=[("../app/templates/dashboard.html", "app/templates")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Optional C-speed loops/protocols we deliberately don't use (we pin
    # asyncio + h11), so excluding them avoids "hidden import not found" noise.
    excludes=["uvloop", "httptools"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="GameGate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,          # windowed app (no console) — matches --noconsole
    disable_windowed_traceback=False,
    icon=["gamegate.ico"],
)
