"""M9: the dashboard session cookie is a signed, expiring token derived from —
but never equal to — the master API token, and it can't be forged or replayed
after expiry."""
from app.security import (
    constant_time_equals,
    issue_session_cookie,
    verify_session_cookie,
)

SECRET = "master-token-value"


def test_cookie_is_not_the_master_token():
    cookie = issue_session_cookie(SECRET)
    assert SECRET not in cookie
    assert cookie.count(".") == 2  # <expiry>.<nonce>.<hmac>


def test_valid_cookie_verifies():
    assert verify_session_cookie(issue_session_cookie(SECRET), SECRET) is True


def test_wrong_secret_rejected():
    # Rotating the master token invalidates every outstanding cookie.
    assert verify_session_cookie(issue_session_cookie(SECRET), "rotated") is False


def test_expired_cookie_rejected():
    expired = issue_session_cookie(SECRET, ttl_seconds=-1)
    assert verify_session_cookie(expired, SECRET) is False


def test_tampered_cookie_rejected():
    cookie = issue_session_cookie(SECRET)
    expiry, nonce, sig = cookie.split(".")
    forged = f"{int(expiry) + 999999}.{nonce}.{sig}"  # extend expiry, keep old sig
    assert verify_session_cookie(forged, SECRET) is False


def test_malformed_cookies_rejected():
    for bad in ["", "nope", "a.b", "a.b.c.d", "notanint.n.s"]:
        assert verify_session_cookie(bad, SECRET) is False


def test_constant_time_equals_handles_non_ascii():
    # Byte compare must not raise on non-ASCII input (round-3 regression).
    assert constant_time_equals("café", "secret") is False
    assert constant_time_equals("x", "x") is True
