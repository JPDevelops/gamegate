"""Gmail over IMAP with an app password — the local, no-OAuth path.

This is the desktop/local way to pull Gmail into GameGate. Instead of a Google
Cloud project + OAuth consent + a restricted-scope security review, the user:

  1. turns on 2-Step Verification, and
  2. generates a 16-character *app password* at
     https://myaccount.google.com/apppasswords

then pastes their address + that app password (exactly like the OpenAI key
flow). We connect straight to Gmail's IMAP endpoint with the Python standard
library (`imaplib`) — no third-party Google packages — poll INBOX read-only for
new mail, and hand each message to the same ingest path every other source uses,
so it gets VIP/keyword/AI triage for free. Credentials never leave this machine.

Only NEW mail (arriving after the poller starts) is ingested — on the first poll
we record the current highest UID as a baseline and ingest nothing, so
connecting doesn't replay the whole inbox as a card parade (the same lesson the
OAuth connector learned live).

Everything here is pure/injectable enough to unit-test on any OS with a fake
IMAP object — no live Google account needed in CI.
"""
import contextlib
import email as email_lib
import imaplib
import logging
import re
import threading
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from app.integrations.gmail_connector import normalize_email

log = logging.getLogger("gamegate.gmail_imap")

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
DEFAULT_POLL_SECONDS = 60
# Per-poll cap: a burst of mail must not stall a single poll or flood ingest.
FETCH_LIMIT = 50


def clean_password(app_password: str) -> str:
    """Google DISPLAYS an app password grouped as 'abcd efgh ijkl mnop'; users
    paste it with the spaces. IMAP wants the bare 16 chars, so strip whitespace."""
    return re.sub(r"\s+", "", app_password or "")


def _open(address, app_password, connector, host, port, timeout):
    """Connect + log in. Raises on failure (caller decides what that means). If
    login fails (e.g. a revoked app password), close the just-opened socket
    before re-raising — otherwise a persistently-bad credential would leak one
    connection per poll while the poller backs off and retries."""
    factory = connector or imaplib.IMAP4_SSL
    conn = factory(host, port, timeout=timeout)
    try:
        conn.login(address, app_password)
    except Exception:
        with contextlib.suppress(Exception):
            conn.logout()
        raise
    return conn


def verify_imap_login(
    address: str, app_password: str, *,
    host: str = IMAP_HOST, port: int = IMAP_PORT, timeout: int = 15, connector=None,
) -> tuple[bool, str]:
    """(ok, note) — attempt a real IMAP login so a bad address/app-password is
    caught at save time, not silently on every poll (same contract as the
    OpenAI-key check). Distinguishes a REJECTED login (ok=False, don't save)
    from a NETWORK problem (ok=True with a soft note — we couldn't check, so
    don't punish the user for being briefly offline)."""
    pw = clean_password(app_password)
    address = (address or "").strip()
    if not address or not pw:
        return False, "Enter your Gmail address and app password."
    factory = connector or imaplib.IMAP4_SSL
    try:
        conn = factory(host, port, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — DNS/TLS/socket: can't verify now
        log.warning("Gmail IMAP unreachable during verify: %s", exc)
        return True, ("Couldn't reach Gmail to check the login right now — saved "
                      "anyway; GameGate will keep trying.")
    try:
        conn.login(address, pw)
        return True, ""
    except imaplib.IMAP4.error:
        return False, ("Gmail rejected that address or app password. Make sure "
                       "2-Step Verification is on and you pasted the 16-character "
                       "app password (not your normal Gmail password).")
    except Exception as exc:  # noqa: BLE001 — unexpected; treat as unverifiable
        log.warning("Gmail IMAP verify error: %s", exc)
        return True, ("Couldn't verify the login right now — saved anyway; "
                      "GameGate will keep trying.")
    finally:
        with contextlib.suppress(Exception):
            conn.logout()


def _decode(raw: str) -> str:
    """Decode a possibly RFC 2047-encoded header (=?utf-8?...?=) to plain text."""
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:  # noqa: BLE001 — malformed encoding → use the raw text
        return raw.strip()


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _looks_encoded(text: str) -> bool:
    """True when the text looks like an undecoded base64/binary body rather than
    prose. We fetch a RAW body slice, and HTML mail commonly encodes the first
    part as base64 — decoding that blindly is unsafe (a normal sentence is also
    all-base64-charset), so instead we DETECT gibberish and drop it. Heuristic:
    base64 wraps at ~76 chars with no spaces, so its 'words' are far longer than
    natural language (~5 chars). A high average token length ⇒ not prose."""
    tokens = text.split()
    if not tokens:
        return False
    avg = sum(len(t) for t in tokens) / len(tokens)
    return avg > 30 and len(text) > 40


def _snippet(raw: bytes) -> str:
    """A short, clean text preview from a raw body fragment: decode, strip HTML
    tags, collapse whitespace, cap length. Best-effort — a messy MIME part just
    yields a shorter/empty snippet, never an error. If the fragment is an
    undecoded base64/binary body (see _looks_encoded), we return "" rather than
    feed gibberish into the keyword rules and the AI classifier — an empty
    snippet is strictly better than garbage (the subject still carries signal)."""
    if not raw:
        return ""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()
    if _looks_encoded(text):
        return ""
    return text[:500]


def _parse_uids(data) -> list[int]:
    """SEARCH returns [b'1 2 3'] (or [None]); turn it into sorted ints."""
    out: list[int] = []
    for chunk in data or []:
        if not chunk:
            continue
        text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
        for tok in text.split():
            with contextlib.suppress(ValueError):
                out.append(int(tok))
    return sorted(out)


def parse_fetch(data) -> tuple[bytes, bytes]:
    """Split a FETCH response into (header_bytes, snippet_bytes). imaplib returns
    each requested body part as a (descriptor, payload) tuple plus stray bytes;
    we route by what the descriptor names."""
    header_bytes, snippet_bytes = b"", b""
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2:
            desc = (item[0] or b"")
            desc = desc.upper() if isinstance(desc, bytes) else b""
            payload = item[1] or b""
            if b"HEADER" in desc:
                header_bytes = payload
            elif b"[1]" in desc or b"TEXT" in desc or b"BODY[" in desc:
                snippet_bytes = payload
            elif not header_bytes:
                header_bytes = payload
    return header_bytes, snippet_bytes


def _message_from_fetch(uid: int, data) -> dict | None:
    """Turn one FETCH response into the message dict normalize_email expects:
    {id, sender, subject, snippet, received_at}."""
    header_bytes, snippet_bytes = parse_fetch(data)
    if not header_bytes:
        return None
    msg = email_lib.message_from_bytes(header_bytes)
    sender = _decode(msg.get("From", "")) or "unknown"
    subject = _decode(msg.get("Subject", "")) or "(no subject)"
    received = datetime.now(UTC)
    with contextlib.suppress(Exception):
        parsed = parsedate_to_datetime(msg.get("Date", ""))
        if parsed is not None:
            received = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return {
        "id": f"imap-{uid}",              # stable → idempotent on (gmail, external_id)
        "sender": sender,
        "subject": subject,
        "snippet": _snippet(snippet_bytes),
        "received_at": received.astimezone(UTC).isoformat(),
    }


# Fetch just what we need: the From/Subject/Date headers (BODY.PEEK so the mail
# is NOT marked read) plus a small slice of the first body part for a snippet.
_FETCH_SPEC = "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] BODY.PEEK[1]<0.2048>)"


def fetch_since(
    address: str, app_password: str, last_uid: int | None, *,
    limit: int = FETCH_LIMIT, host: str = IMAP_HOST, port: int = IMAP_PORT,
    timeout: int = 30, connector=None,
) -> tuple[list[dict], int]:
    """Return (new_messages, new_last_uid). When last_uid is None this is the
    baseline call: it records the current highest UID and returns NO messages,
    so we never replay the existing inbox. Otherwise it returns up to `limit`
    messages with UID > last_uid, OLDEST first, advancing new_last_uid only past
    what was actually fetched — a burst larger than `limit` is caught up over
    successive polls instead of skipping the older messages."""
    pw = clean_password(app_password)
    conn = _open(address, pw, connector, host, port, timeout)
    try:
        conn.select("INBOX", readonly=True)
        if last_uid is None:
            uids = _parse_uids(conn.uid("SEARCH", None, "ALL")[1])
            return [], (max(uids) if uids else 0)
        found = [u for u in _parse_uids(conn.uid("SEARCH", None, "UID",
                                                 f"{last_uid + 1}:*")[1]) if u > last_uid]
        # Take the OLDEST `limit` this cycle and advance only to what we fetch, so
        # a backlog > limit isn't jumped over and lost (the rest arrives next poll).
        batch = found[:limit]
        new_last = max([last_uid, *batch])
        messages: list[dict] = []
        for uid in batch:
            try:
                data = conn.uid("FETCH", str(uid), _FETCH_SPEC)[1]
                msg = _message_from_fetch(uid, data)
            except Exception:  # noqa: BLE001 — one bad message must not sink the poll
                log.exception("Gmail IMAP: failed to fetch UID %s", uid)
                msg = None
            if msg:
                messages.append(msg)
        return messages, new_last
    finally:
        with contextlib.suppress(Exception):
            conn.logout()


def _default_ingest(payload: dict) -> None:
    """Ingest one normalized email through the SAME pipeline as /events (VIP,
    keyword, AI classify, routing) by calling IngestService directly — no HTTP
    self-call. Imported lazily to avoid an import cycle at module load."""
    from app.config import get_settings
    from app.db import get_database
    from app.models.event import EventIn
    from app.services.ingest_service import IngestService

    IngestService(get_database(), get_settings()).ingest(EventIn(**payload))


def _record_health(ok: bool, detail: str = "") -> None:
    """Best-effort heartbeat so the dashboard shows real Gmail health (and
    degrades if IMAP starts failing), matching the other connectors."""
    with contextlib.suppress(Exception):
        from app.db import get_database
        from app.services.repositories import ConnectorHealthRepository

        ConnectorHealthRepository(get_database()).record("gmail", ok, detail)


def _run(stop: threading.Event, address, app_password, ingest, poll_seconds) -> None:
    log.info("Gmail IMAP poller started for %s (every %ss)", address, poll_seconds)
    last_uid: int | None = None
    while not stop.is_set():
        try:
            messages, last_uid = fetch_since(address, app_password, last_uid)
            for payload in (normalize_email(m) for m in messages):
                try:
                    ingest(payload)
                except Exception:  # noqa: BLE001 — one bad ingest, keep going
                    log.exception("Gmail IMAP: ingest failed")
            if messages:
                log.info("Gmail IMAP: ingested %d new message(s)", len(messages))
            _record_health(True)
        except imaplib.IMAP4.error as exc:
            # Auth revoked / app password removed: log clearly and back off — the
            # user must reconnect. Don't spin tightly on a permanent failure.
            log.error("Gmail IMAP auth/login failed: %s", exc)
            _record_health(False, "Gmail rejected the login — reconnect the app password")
            stop.wait(max(poll_seconds, 300))
            continue
        except Exception as exc:  # noqa: BLE001 — network blips etc.; retry next cycle
            log.exception("Gmail IMAP poll failed; will retry")
            _record_health(False, str(exc)[:200])
        stop.wait(poll_seconds)
    log.info("Gmail IMAP poller stopped")


class _Handle:
    thread: threading.Thread | None = None
    stop: threading.Event | None = None
    address: str = ""


_handle = _Handle()
_lock = threading.Lock()


def configure(
    address: str, app_password: str, enabled: bool, *,
    ingest=None, poll_seconds: int = DEFAULT_POLL_SECONDS,
) -> bool:
    """Start/stop/restart the background poller to match the desired state. Safe
    to call repeatedly (from the settings endpoint or startup). Returns True if a
    poller is now running. No creds or not enabled → any running poller is
    stopped and this returns False."""
    with _lock:
        if _handle.thread and _handle.thread.is_alive() and _handle.stop:
            _handle.stop.set()  # tear down the old one before starting a new one
        _handle.thread = None
        pw = clean_password(app_password)
        address = (address or "").strip()
        if not (enabled and address and pw):
            log.info("Gmail IMAP poller not running (enabled=%s, creds=%s)",
                     enabled, bool(address and pw))
            return False
        stop = threading.Event()
        thread = threading.Thread(
            target=_run, args=(stop, address, pw, ingest or _default_ingest, poll_seconds),
            name="gamegate-gmail-imap", daemon=True,
        )
        _handle.thread, _handle.stop, _handle.address = thread, stop, address
        thread.start()
        return True


def stop() -> None:
    """Stop the poller if running (used on reconfigure/shutdown)."""
    with _lock:
        if _handle.stop:
            _handle.stop.set()
        _handle.thread = None


def is_running() -> bool:
    return bool(_handle.thread and _handle.thread.is_alive())
