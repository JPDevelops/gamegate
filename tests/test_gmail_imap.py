"""Local Gmail path: IMAP + app password (no OAuth, no live Google account).

Everything is exercised against a fake IMAP object so CI never touches the
network — the same way the OpenAI-key check is tested with a mock transport.
"""
import imaplib

from app import db as db_module
from app.integrations import gmail_imap
from app.integrations.gmail_connector import normalize_email
from app.services.settings_service import SettingsService

# A realistic imaplib FETCH response: a (descriptor, payload) tuple for the
# header fields, another for the body snippet, then the trailing b")".
_HEADER = (
    b"From: =?utf-8?q?Alice_Example?= <alice@example.com>\r\n"
    b"Subject: Servers will be suspended\r\n"
    b"Date: Mon, 25 Aug 2026 10:00:00 +0000\r\n\r\n"
)
_FETCH = {
    "4": [(b"4 (BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {%d}" % len(_HEADER), _HEADER),
          (b" BODY[1] {26}", b"<p>Please update payment</p>"), b")"],
    "5": [(b"5 (BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {%d}" % len(_HEADER), _HEADER),
          (b" BODY[1] {5}", b"hello"), b")"],
}


class FakeIMAP:
    """Minimal stand-in for imaplib.IMAP4_SSL covering login/select/uid/logout."""

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.logged_in = False

    def login(self, user, password):
        if password != "goodpassword":
            raise imaplib.IMAP4.error("b'[AUTHENTICATIONFAILED] Invalid credentials'")
        self.logged_in = True
        return ("OK", [b"logged in"])

    def select(self, mailbox="INBOX", readonly=False):
        return ("OK", [b"3"])

    def uid(self, command, *args):
        if command == "SEARCH":
            if "ALL" in args:
                return ("OK", [b"1 2 3"])       # baseline: highest existing UID = 3
            return ("OK", [b"4 5"])              # new mail past the baseline
        if command == "FETCH":
            return ("OK", _FETCH[args[0]])
        return ("OK", [b""])

    def logout(self):
        return ("BYE", [b"bye"])


def _factory(host, port, timeout=None):
    return FakeIMAP(host, port, timeout)


def test_clean_password_strips_the_spaces_google_shows():
    assert gmail_imap.clean_password("abcd efgh ijkl mnop") == "abcdefghijklmnop"
    assert gmail_imap.clean_password("  a b\tc\n") == "abc"
    assert gmail_imap.clean_password("") == ""


def test_verify_login_accepts_good_rejects_bad():
    ok, note = gmail_imap.verify_imap_login("me@gmail.com", "good password",
                                            connector=lambda *a, **k: FakeIMAP(*a, **k))
    assert ok is True and note == ""

    ok, note = gmail_imap.verify_imap_login("me@gmail.com", "wrong", connector=_factory)
    assert ok is False and "rejected" in note.lower()


def test_verify_login_requires_both_fields():
    assert gmail_imap.verify_imap_login("", "pw", connector=_factory)[0] is False
    assert gmail_imap.verify_imap_login("me@gmail.com", "", connector=_factory)[0] is False


def test_verify_login_network_error_is_soft_accept():
    def _boom(host, port, timeout=None):
        raise OSError("name resolution failed")
    ok, note = gmail_imap.verify_imap_login("me@gmail.com", "goodpassword", connector=_boom)
    assert ok is True and "couldn't" in note.lower()  # can't check ≠ wrong


def test_message_from_fetch_parses_headers_and_snippet():
    msg = gmail_imap._message_from_fetch(4, _FETCH["4"])
    assert msg["id"] == "imap-4"
    assert "alice@example.com" in msg["sender"]
    assert msg["subject"] == "Servers will be suspended"
    assert "update payment" in msg["snippet"]      # HTML tags stripped
    assert "<p>" not in msg["snippet"]
    assert msg["received_at"] == "2026-08-25T10:00:00+00:00"


def test_fetch_since_baseline_then_incremental():
    # First call (last_uid=None): record the tail, ingest nothing.
    msgs, last = gmail_imap.fetch_since("me@gmail.com", "goodpassword", None, connector=_factory)
    assert msgs == [] and last == 3

    # Next call: only UIDs past the baseline come back.
    msgs, last = gmail_imap.fetch_since("me@gmail.com", "goodpassword", 3, connector=_factory)
    assert last == 5
    assert [m["id"] for m in msgs] == ["imap-4", "imap-5"]


class BurstIMAP:
    """Fake with a large backlog of new UIDs to exercise the per-poll cap."""

    def __init__(self, host, port, timeout=None):
        pass

    def login(self, user, password):
        return ("OK", [b"ok"])

    def select(self, mailbox="INBOX", readonly=False):
        return ("OK", [b"123"])

    def uid(self, command, *args):
        if command == "SEARCH":
            if "ALL" in args:
                return ("OK", [b"1 2 3"])
            # 120 messages waiting past the baseline: UIDs 4..123.
            return ("OK", [" ".join(str(i) for i in range(4, 124)).encode()])
        if command == "FETCH":
            uid = args[0].encode()
            hdr = (b"From: a@b.com\r\nSubject: msg " + uid +
                   b"\r\nDate: Mon, 25 Aug 2026 10:00:00 +0000\r\n\r\n")
            return ("OK", [(b"%s (BODY[HEADER.FIELDS...] {%d}" % (uid, len(hdr)), hdr), b")"])
        return ("OK", [b""])


def test_fetch_since_catches_up_a_burst_without_dropping():
    """A backlog larger than the per-poll limit must be caught up over successive
    polls, OLDEST first — never jumped over. Regression guard: advancing
    new_last to max(found) while only fetching the newest `limit` silently lost
    the older messages."""
    f = lambda *a, **k: BurstIMAP(*a, **k)  # noqa: E731
    msgs, last = gmail_imap.fetch_since("me@gmail.com", "goodpassword", 3, limit=50, connector=f)
    assert [m["id"] for m in msgs][0] == "imap-4"     # oldest first
    assert len(msgs) == 50
    assert last == 53                                  # advanced only past what we fetched…
    # …so the next poll resumes from 53 and picks up the next batch — nothing lost.
    msgs2, last2 = gmail_imap.fetch_since("me@gmail.com", "goodpassword", 53, limit=50, connector=f)
    assert msgs2[0]["id"] == "imap-54" and last2 == 103
    msgs3, last3 = gmail_imap.fetch_since("me@gmail.com", "goodpassword", 103, limit=50, connector=f)
    assert [m["id"] for m in msgs3] == [f"imap-{u}" for u in range(104, 124)] and last3 == 123


def test_snippet_drops_encoded_gibberish_keeps_prose():
    prose = b"Hi there, please review the attached invoice and reply by Friday."
    assert "invoice" in gmail_imap._snippet(prose)
    assert gmail_imap._snippet(b"<p>Hello <b>world</b> friend</p>") == "Hello world friend"
    # A raw base64 body slice (one long token, no spaces) is gibberish → dropped,
    # so it never pollutes keyword/AI classification.
    b64 = b"TWFueSBoYW5kcyBtYWtlIGxpZ2h0IHdvcmsu" * 4
    assert gmail_imap._snippet(b64) == ""


def test_fetched_message_normalizes_into_an_event_payload():
    msg = gmail_imap._message_from_fetch(4, _FETCH["4"])
    payload = normalize_email(msg)
    assert payload["source"] == "gmail"
    assert payload["external_id"] == "imap-4"
    assert payload["title"] == "Servers will be suspended"
    # normalize_email must produce a payload EventIn accepts.
    from app.models.event import EventIn
    EventIn(**payload)


# ---- settings storage: the app password is a write-only secret ----

def test_gmail_credentials_roundtrip_password_never_returned(tmp_path):
    db_module.init_database(str(tmp_path / "t.db"))
    svc = SettingsService(db_module.get_database())

    svc.set_gmail_credentials("me@gmail.com", "abcdefghijklmnop")
    assert svc.get_gmail_credentials() == ("me@gmail.com", "abcdefghijklmnop")

    alld = svc.get_all()
    assert alld["gmail_address"] == "me@gmail.com"
    assert alld["gmail_app_password_set"] is True
    assert "abcdefghijklmnop" not in str(alld)   # the secret never leaks via get_all

    svc.set_gmail_credentials("", "")            # clear
    assert svc.get_all()["gmail_app_password_set"] is False


# ---- the /settings/gmail endpoint ----

def _accept(monkeypatch):
    from app.api import settings as settings_api
    monkeypatch.setattr(settings_api, "verify_imap_login", lambda addr, pw, **k: (True, ""))
    monkeypatch.setattr(settings_api, "gmail_configure", lambda *a, **k: True)


def test_connect_gmail_stores_and_reports_connected(client, monkeypatch):
    _accept(monkeypatch)
    r = client.post("/settings/gmail", json={
        "enabled": True, "address": "me@gmail.com", "app_password": "abcd efgh ijkl mnop",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["app_password_set"] is True
    assert body["address"] == "me@gmail.com"

    # Password stored WITHOUT the display spaces, and never returned via /settings.
    s = client.get("/settings").json()
    assert s["gmail_app_password_set"] is True
    assert "abcd efgh" not in str(s) and "abcdefghijklmnop" not in str(s)

    conns = client.get("/connections").json()
    assert conns["gmail"]["state"] == "connected"
    assert conns["gmail"]["kind"] == "imap"


def test_bad_login_is_rejected_and_not_stored(client, monkeypatch):
    from app.api import settings as settings_api
    monkeypatch.setattr(settings_api, "gmail_configure", lambda *a, **k: True)
    monkeypatch.setattr(settings_api, "verify_imap_login",
                        lambda addr, pw, **k: (False, "Gmail rejected that address or app password."))
    r = client.post("/settings/gmail", json={
        "enabled": True, "address": "me@gmail.com", "app_password": "wrong",
    })
    assert r.status_code == 400
    assert "rejected" in r.json()["detail"].lower()
    s = client.get("/settings").json()
    assert s["gmail_app_password_set"] is False
    assert s["gmail_enabled"] is False


def test_disconnect_turns_polling_off_but_keeps_saved_password(client, monkeypatch):
    _accept(monkeypatch)
    client.post("/settings/gmail", json={
        "enabled": True, "address": "me@gmail.com", "app_password": "abcdefghijklmnop"})
    # Disconnect = enabled:false, no password field → creds stay on file.
    r = client.post("/settings/gmail", json={"enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False and body["app_password_set"] is True
    conns = client.get("/connections").json()
    assert conns["gmail"]["state"] == "needs setup"   # saved but off
    assert conns["gmail"]["can_connect"] is True


def test_cannot_enable_without_credentials(client, monkeypatch):
    _accept(monkeypatch)
    r = client.post("/settings/gmail", json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["enabled"] is False               # nothing to enable yet
