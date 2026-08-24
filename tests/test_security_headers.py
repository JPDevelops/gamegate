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


def test_keyless_dashboard_views_do_not_lock_out_the_owner(tmp_path, monkeypatch):
    """MINOR: opening a bookmarked keyless /app just shows the 401 login page;
    refreshing it repeatedly must NOT trip the auth throttle (a real ?key= guess
    still counts). Otherwise the owner locks their own IP by viewing the login."""
    from fastapi.testclient import TestClient

    from app import db as db_module
    from app.config import get_settings
    from app.main import app

    monkeypatch.setenv("GAMEGATE_API_TOKEN", "secret")
    get_settings.cache_clear()
    db_module.init_database(str(tmp_path / "t.db"))
    with TestClient(app) as c:
        # 20 keyless login-page views — all 401, none throttled
        codes = [c.get("/app").status_code for _ in range(20)]
        assert set(codes) == {401}
        assert 429 not in codes
        # a genuine bad-key guess is still counted toward the throttle
        guesses = [c.get("/app", params={"key": "wrong"}).status_code for _ in range(12)]
        assert 429 in guesses
    get_settings.cache_clear()


def test_xff_only_trusted_from_local_proxy():
    """X-Forwarded-For is honored only when the peer is our loopback proxy;
    a direct caller cannot mint identities or frame a victim via XFF (M3)."""
    from types import SimpleNamespace

    from app.middleware import AuthRateLimitMiddleware

    mw = AuthRateLimitMiddleware.__new__(AuthRateLimitMiddleware)

    def req(peer, xff):
        return SimpleNamespace(
            headers={"x-forwarded-for": xff} if xff else {},
            client=SimpleNamespace(host=peer),
        )

    # From nginx (127.0.0.1): trust the forwarded client.
    assert mw._client(req("127.0.0.1", "203.0.113.7")) == "203.0.113.7"
    # Direct caller spoofing XFF: ignored, keyed by the real peer instead —
    # so rotating a single-entry XFF can't mint fresh identities...
    assert mw._client(req("203.0.113.9", "1.1.1.1")) == "203.0.113.9"
    # ...and can't frame a victim's IP either.
    assert mw._client(req("203.0.113.9", "9.9.9.9")) == "203.0.113.9"


def test_rate_limit_not_reset_by_unauthenticated_success(tmp_path, monkeypatch):
    """M1: interleaving a successful GET /health between bad token guesses must
    NOT reset the failure counter — otherwise the throttle is trivially bypassed.
    Fails on the old 'clear on any 2xx' code."""
    from fastapi.testclient import TestClient

    from app import db as db_module
    from app.config import get_settings
    from app.main import app

    monkeypatch.setenv("GAMEGATE_API_TOKEN", "secret")
    get_settings.cache_clear()
    db_module.init_database(str(tmp_path / "t.db"))
    with TestClient(app) as c:
        got429 = False
        for _ in range(14):
            c.post("/events", json={})   # 401 (bad/no token)
            c.get("/health")             # 200 — must not reset the counter
            if c.post("/events", json={}).status_code == 429:
                got429 = True
                break
        assert got429  # still throttled despite the interleaved successes
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


def test_rate_limit_uses_last_xff_hop_not_spoofable():
    """From a trusted proxy peer, identity is the LAST XFF hop (the value nginx
    appended), so rotating a spoofed LEFT entry can't mint fresh quota. This is a
    real unit test of _client(): the previous integration version was vacuous —
    under TestClient the peer is 'testclient' (untrusted), so XFF was never
    consulted and it would pass even if the code picked the spoofable first hop."""
    from types import SimpleNamespace

    from app.middleware import AuthRateLimitMiddleware

    mw = AuthRateLimitMiddleware.__new__(AuthRateLimitMiddleware)

    def req(peer, xff):
        return SimpleNamespace(
            headers={"x-forwarded-for": xff} if xff else {},
            client=SimpleNamespace(host=peer),
        )

    # nginx (127.0.0.1) appended the real client 1.1.1.1 after a spoofed 9.9.9.x.
    # Identity is always the last hop, no matter how the attacker rotates the left
    # entry — so all these requests key to the SAME identity and share one quota.
    ids = {mw._client(req("127.0.0.1", f"9.9.9.{i}, 1.1.1.1")) for i in range(20)}
    assert ids == {"1.1.1.1"}


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
