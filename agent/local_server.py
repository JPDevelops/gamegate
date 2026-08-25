"""Embedded GameGate server for LOCAL mode (no cloud, no config).

Runs the full FastAPI app in-process on 127.0.0.1 so the desktop app is
self-contained: there is no separate server to run, no server URL to paste, and
no token to enter. Data lives in a local SQLite file next to the config
(%LOCALAPPDATA%\\GameGate on Windows), and the API token is generated once and
kept in the local config — the user never sees it.

This is what makes "download the .msix, launch it, it just works" true: the
tray process starts this server, then points its own API client + the dashboard
window at http://127.0.0.1:<port>.
"""
import asyncio
import contextlib
import logging
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request

log = logging.getLogger("gamegate.local")

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
# config_token values that mean "not a real token yet" — treat as unset so we
# mint one instead of trying to authenticate with a placeholder.
_PLACEHOLDER_PREFIXES = ("SAME-VALUE", "YOUR-", "CHANGEME", "<")


def _is_placeholder(token: str) -> bool:
    return (not token) or token.startswith(_PLACEHOLDER_PREFIXES)


def find_free_port(preferred: int = DEFAULT_PORT, wait_attempts: int = 12) -> int:
    """Return the STABLE preferred port, waiting briefly for it to free up.

    Stability matters: the dashboard window is a separate process that connects
    to this port, so if the port changed on every restart, an open window (or an
    auto-update restart) would point at a dead port ("couldn't reach GameGate").
    On restart the previous instance releases the port within a moment, so we
    retry `preferred` for a few seconds before falling back to an OS-assigned
    port (only if something else is genuinely squatting on it)."""
    for _ in range(wait_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((HOST, preferred))
                return preferred
            except OSError:
                time.sleep(0.5)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))  # last resort: any free port
        return probe.getsockname()[1]


def ensure_local_token(config: dict, save) -> str:
    """A stable per-install token, generated once and persisted to the local
    config so the tray, the embedded server, and the dashboard window all agree.
    The user never types it. `save` is detector.save_config_updates."""
    token = str(config.get("api_token") or "")
    if _is_placeholder(token):
        token = secrets.token_urlsafe(24)
        config["api_token"] = token
        with contextlib.suppress(Exception):
            save({"api_token": token})
    return token


def start_local_server(config: dict, db_path: str, save, port: int = DEFAULT_PORT):
    """Start the embedded FastAPI server in a daemon thread and return
    (base_url, token). Blocks until the server answers /health (bounded).

    Mutates `config` with the resolved api_url/api_token and persists them via
    `save` so the separate `--window` process reads the same values.
    """
    token = ensure_local_token(config, save)
    chosen = find_free_port(port)
    base_url = f"http://{HOST}:{chosen}"

    # Configure the app via env BEFORE importing it — Settings are read once and
    # cached (lru_cache). GAMEGATE_ENV=production keeps the app's fail-closed
    # auth guard happy now that a real token is set.
    os.environ["GAMEGATE_API_TOKEN"] = token
    os.environ["GAMEGATE_DB_PATH"] = db_path
    os.environ.setdefault("GAMEGATE_ENV", "production")

    with contextlib.suppress(OSError):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    import uvicorn  # heavy import, kept out of module import time

    # Pin the pure-Python loop/protocol so the frozen build never needs the
    # optional C extensions (uvloop/httptools) that complicate PyInstaller.
    # log_config=None is CRITICAL for the packaged (--noconsole) build: uvicorn's
    # default log config builds a colorized formatter that calls
    # sys.stdout.isatty(), but a windowed PyInstaller app has sys.stdout = None,
    # so that raises AttributeError -> "Unable to configure formatter 'default'"
    # and the server never starts. We use the app's own logging instead.
    server = uvicorn.Server(uvicorn.Config(
        "app.main:app", host=HOST, port=chosen,
        log_level="warning", access_log=False, log_config=None,
        loop="asyncio", http="h11", lifespan="on",
    ))

    def _serve():
        try:
            # Windows: uvicorn in a background (non-main) thread MUST use the
            # Selector event loop. The default Proactor loop can only be driven
            # from the main thread, so in a daemon thread it silently fails to
            # serve — the embedded server never binds and the app can't reach it
            # (the exact "server on a port but 404 from something else" symptom).
            # No effect off Windows.
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            server.run()
        except Exception:  # noqa: BLE001 — surface, don't kill the tray
            log.exception("Embedded GameGate server stopped unexpectedly")

    threading.Thread(target=_serve, name="gamegate-server", daemon=True).start()

    if wait_until_ready(base_url):
        log.info("Embedded server ready at %s (db=%s)", base_url, db_path)
    else:
        log.warning("Embedded server not ready yet at %s — continuing anyway", base_url)

    config["api_url"] = base_url
    config["api_token"] = token
    with contextlib.suppress(Exception):
        save({"api_url": base_url, "api_token": token, "local_port": chosen})
    return base_url, token


def wait_until_ready(base_url: str, timeout: float = 25.0) -> bool:
    """Poll /health until the server answers or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001 — not up yet; keep polling
            pass
        time.sleep(0.25)
    return False
