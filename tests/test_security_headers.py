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
    import pytest  # noqa


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
