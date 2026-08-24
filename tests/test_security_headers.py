"""Pre-launch checklist: security headers, HSTS, auth rate limiting."""


def test_security_headers_present(client):
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in r.headers
    assert r.headers["Referrer-Policy"] == "no-referrer"


def test_hsts_only_over_https(client):
    assert "Strict-Transport-Security" not in client.get("/health").headers
    r = client.get("/health", headers={"X-Forwarded-Proto": "https"})
    assert "Strict-Transport-Security" in r.headers


def test_auth_rate_limit_blocks_after_repeated_failures(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import db as db_module
    from app.config import get_settings
    from app.main import app

    monkeypatch.setenv("GAMEGATE_API_TOKEN", "secret")
    get_settings.cache_clear()
    db_module.init_database(str(tmp_path / "t.db"))
    with TestClient(app) as c:
        codes = [c.post("/events", json={}).status_code for _ in range(12)]
        assert 429 in codes  # brute force eventually throttled
    get_settings.cache_clear()


def test_rate_limited_429_still_carries_security_headers(tmp_path, monkeypatch):
    """The header middleware must wrap the rate limiter, so even a throttled
    429 ships CSP + nosniff instead of a bare body (M11)."""
    from fastapi.testclient import TestClient

    from app import db as db_module
    from app.config import get_settings
    from app.main import app

    monkeypatch.setenv("GAMEGATE_API_TOKEN", "secret")
    get_settings.cache_clear()
    db_module.init_database(str(tmp_path / "t.db"))
    with TestClient(app) as c:
        r = None
        for _ in range(14):
            r = c.post("/events", json={})
            if r.status_code == 429:
                break
        assert r.status_code == 429
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert "Content-Security-Policy" in r.headers
    get_settings.cache_clear()


def test_rate_limit_uses_last_xff_hop_not_spoofable(tmp_path, monkeypatch):
    """XFF spoof must NOT grant fresh quota: identity is the last (nginx) hop."""
    from fastapi.testclient import TestClient

    from app import db as db_module
    from app.config import get_settings
    from app.main import app

    monkeypatch.setenv("GAMEGATE_API_TOKEN", "secret")
    get_settings.cache_clear()
    db_module.init_database(str(tmp_path / "t.db"))
    with TestClient(app) as c:
        # simulate nginx appending the real client (1.1.1.1) after a spoofed value
        got429 = False
        for i in range(14):
            r = c.post("/events", json={},
                       headers={"X-Forwarded-For": f"9.9.9.{i}, 1.1.1.1"})
            if r.status_code == 429:
                got429 = True
                break
        assert got429  # rotating the spoofed left entry did not evade the limiter
    get_settings.cache_clear()


def test_authenticated_responses_are_not_cacheable(client):
    """Responses to credentialed requests carry personal data (and the token in
    /app?key=) — they must be Cache-Control: no-store so shared/disk caches
    don't retain them. Public pages stay cacheable."""
    public = client.get("/health")
    assert public.headers.get("Cache-Control") != "no-store"
    for creds in (
        {"headers": {"X-GameGate-Token": "x"}},
        {"cookies": {"gamegate_token": "x"}},
        {"params": {"key": "x"}},
    ):
        r = client.get("/health", **creds)
        assert r.headers.get("Cache-Control") == "no-store"


def test_deeply_nested_json_is_400_not_500(client):
    nested = "{\"a\":" * 3000 + "1" + "}" * 3000
    r = client.post("/events", data=nested, headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422)  # clean client error, never 500


def test_non_ascii_token_raises_401_not_crash(monkeypatch):
    """Server receives header bytes as latin-1; a non-ASCII token must yield a
    clean 401, never crash compare_digest (the round-3 finding)."""
    import pytest
    from fastapi import HTTPException

    from app.config import get_settings
    from app.security import require_api_token

    monkeypatch.setenv("GAMEGATE_API_TOKEN", "secret")
    get_settings.cache_clear()
    for bad in ["caf\u00e9", "\U0001f600", "\u202e", "guess"]:
        with pytest.raises(HTTPException) as exc:
            require_api_token(x_gamegate_token=bad, gamegate_token=None)
        assert exc.value.status_code == 401
    get_settings.cache_clear()
