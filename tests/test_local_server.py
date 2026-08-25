"""Tests for local mode: the embedded FastAPI server the desktop app runs on
127.0.0.1 so it works with no cloud and no configuration."""
import socket
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import local_server


def test_is_placeholder_treats_examples_as_unset():
    assert local_server._is_placeholder("")
    assert local_server._is_placeholder("SAME-VALUE-AS-GAMEGATE_API_TOKEN")
    assert local_server._is_placeholder("YOUR-TOKEN")
    assert local_server._is_placeholder("<paste token>")
    assert not local_server._is_placeholder("aRealLookingToken_123")


def test_find_free_port_prefers_requested_when_free():
    # Ask for an OS-assigned free port, then confirm find_free_port hands it back
    # when it's available.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert local_server.find_free_port(free) == free


def test_find_free_port_falls_back_when_taken():
    # Hold a port so `preferred` is unavailable; find_free_port must return a
    # DIFFERENT, usable port instead of the taken one.
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        busy = taken.getsockname()[1]
        got = local_server.find_free_port(busy, wait_attempts=1)  # don't wait out retries
        assert got != busy
        # And the returned port is actually bindable.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", got))


def test_ensure_local_token_generates_and_persists_once():
    saved = {}
    config = {"api_token": "SAME-VALUE-AS-GAMEGATE_API_TOKEN"}

    token = local_server.ensure_local_token(config, lambda upd: saved.update(upd))
    assert token and not local_server._is_placeholder(token)
    assert config["api_token"] == token
    assert saved["api_token"] == token          # persisted for the window process

    # A second call with a real token leaves it untouched (stable per install).
    saved.clear()
    again = local_server.ensure_local_token(config, lambda upd: saved.update(upd))
    assert again == token
    assert saved == {}                          # nothing re-written


def test_embedded_server_boots_and_serves(tmp_path, monkeypatch):
    """The whole point of local mode: start_local_server actually stands up the
    real FastAPI app on localhost and answers /health, with api_url/api_token
    filled into config and persisted. Exercises the wiring end-to-end (the part
    a Windows-only .exe build can't be unit-tested for)."""
    from app import db as db_module

    # Let the embedded server's lifespan initialize its own DB at our temp path.
    monkeypatch.setattr(db_module, "_database", None, raising=False)

    saved = {}
    config = {"api_token": "", "local_mode": True}
    db_path = str(tmp_path / "gamegate.db")

    base, token = local_server.start_local_server(
        config, db_path, lambda upd: saved.update(upd),
    )

    assert base.startswith("http://127.0.0.1:")
    assert config["api_url"] == base
    assert saved["api_url"] == base and saved["api_token"] == token  # window can read it

    # It is genuinely up and answering.
    assert local_server.wait_until_ready(base, timeout=15)
    with urllib.request.urlopen(f"{base}/health", timeout=3) as resp:
        assert resp.status == 200
        assert b'"status":"ok"' in resp.read().replace(b" ", b"")


def test_embedded_server_boots_with_no_stdout(tmp_path, monkeypatch):
    """The packaged app runs --noconsole, so sys.stdout is None. uvicorn's
    default log config builds a colorized formatter that calls
    sys.stdout.isatty() -> AttributeError, so the server must be started with
    log_config=None. This reproduces that exact condition (which no console-based
    test otherwise hits) and asserts the server still comes up."""
    from app import db as db_module

    monkeypatch.setattr(db_module, "_database", None, raising=False)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    base, _ = local_server.start_local_server(
        {"api_token": ""}, str(tmp_path / "gg.db"), lambda upd: None,
    )
    # Restore stdout so the assertion output is visible.
    monkeypatch.undo()
    assert local_server.wait_until_ready(base, timeout=15)
