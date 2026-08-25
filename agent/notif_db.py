"""Capture Windows notifications WITHOUT app packaging.

Windows logs every toast notification (Discord, Slack, email, everything) into a
per-user SQLite database:

    %LOCALAPPDATA%\\Microsoft\\Windows\\Notifications\\wpndatabase.db

We read that file directly — no MSIX/app identity, no permission grant, no
admin. This is what lets GameGate ship as a normal "just download and run" app
instead of a signed, cert-trusted package. Nothing here is Windows-API-specific,
so the parsing is unit-tested on Linux against a synthetic database.

The reader polls for NEW rows only (rows whose autoincrement "Order" is higher
than the max seen at startup), so opening GameGate doesn't replay your history.
"""
import contextlib
import gzip
import logging
import os
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from windows_notifications import map_notification_to_event

log = logging.getLogger("gamegate.notifdb")

# FILETIME epoch (1601-01-01) to Unix epoch (1970-01-01), in seconds.
_FILETIME_EPOCH_OFFSET = 11644473600


def db_path() -> Path:
    base = os.environ.get("LOCALAPPDATA", str(Path.home()))
    return Path(base) / "Microsoft" / "Windows" / "Notifications" / "wpndatabase.db"


def filetime_to_iso(filetime: int) -> str:
    """Windows FILETIME (100 ns ticks since 1601) → ISO-8601 UTC. Falls back to
    'now' for a missing/zero/garbage value so an event always has a timestamp."""
    try:
        seconds = int(filetime) / 10_000_000 - _FILETIME_EPOCH_OFFSET
        if seconds <= 0:
            raise ValueError
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return datetime.now(UTC).isoformat()


def parse_payload(payload) -> tuple[str, str]:
    """Extract (title, body) from a toast Payload. The payload is the toast XML;
    the first <text> is the title, the rest join into the body. Handles bytes,
    str, and gzip-compressed payloads; returns ('', '') for anything unparseable
    (tiles/badges/binary formats we don't handle)."""
    raw = payload
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, bytes):
        if raw[:2] == b"\x1f\x8b":  # gzip magic
            with contextlib.suppress(OSError, EOFError):
                raw = gzip.decompress(raw)
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str) or "<" not in raw:
        return "", ""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return "", ""
    # Tag names may be namespaced; match on the local name "text".
    texts = [
        (el.text or "").strip()
        for el in root.iter()
        if el.tag.rsplit("}", 1)[-1] == "text" and (el.text or "").strip()
    ]
    if not texts:
        return "", ""
    return texts[0], "\n".join(texts[1:])


class NotificationDbReader:
    """Poll wpndatabase.db and post each NEW toast to GameGate as an event.

    Call run(stop) from a daemon thread. No-ops safely if the database isn't
    present (non-Windows, or notifications never used)."""

    def __init__(self, post_event, poll_seconds: int = 4) -> None:
        self.post_event = post_event
        self.poll_seconds = poll_seconds
        self._last_order = 0

    def run(self, stop) -> None:
        if not db_path().exists():
            log.warning("Notification database not found at %s — capture off", db_path())
            return
        # Start from the current tail so we capture notifications that arrive
        # AFTER launch, not the whole backlog.
        self._last_order = self._max_order()
        log.info("Notification capture started (reading %s from order %d)",
                 db_path().name, self._last_order)
        while not stop.is_set():
            try:
                self._poll_once()
            except Exception:  # noqa: BLE001 — one bad poll must not kill the loop
                log.exception("Notification DB poll failed; continuing")
            stop.wait(self.poll_seconds)

    def _snapshot(self) -> str | None:
        """Copy the DB (+ WAL/SHM) to a temp file so we never fight the OS for a
        lock and still see un-checkpointed rows. Returns the temp path or None."""
        src = db_path()
        if not src.exists():
            return None
        tmp_dir = tempfile.mkdtemp(prefix="gg_notif_")
        dst = os.path.join(tmp_dir, "wpndatabase.db")
        copied = False
        for suffix in ("", "-wal", "-shm"):
            s = Path(str(src) + suffix)
            if s.exists():
                try:
                    shutil.copy2(s, dst + suffix)
                    copied = copied or suffix == ""
                except OSError:
                    pass
        return dst if copied else None

    def _query(self, sql: str, params: tuple):
        dst = self._snapshot()
        if not dst:
            return []
        try:
            conn = sqlite3.connect(dst)
            conn.row_factory = sqlite3.Row
            try:
                return conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            log.warning("Reading notification DB failed: %s", exc)
            return []
        finally:
            with contextlib.suppress(OSError):
                shutil.rmtree(os.path.dirname(dst), ignore_errors=True)

    def _max_order(self) -> int:
        rows = self._query('SELECT MAX("Order") AS m FROM Notification', ())
        if rows and rows[0]["m"] is not None:
            return int(rows[0]["m"])
        return 0

    def _poll_once(self) -> None:
        rows = self._query(
            'SELECT n."Order" AS ord, n.Payload AS payload, n.ArrivalTime AS arrival, '
            "h.PrimaryId AS app "
            "FROM Notification n "
            "LEFT JOIN NotificationHandler h ON n.HandlerId = h.RecordId "
            'WHERE n."Order" > ? ORDER BY n."Order"',
            (self._last_order,),
        )
        for row in rows:
            order = int(row["ord"] or 0)
            self._last_order = max(self._last_order, order)
            app = row["app"] or ""
            title, body = parse_payload(row["payload"])
            if not (title or body):
                continue  # tile/badge/empty — nothing to recap
            payload = map_notification_to_event(
                app, title, body, str(order), filetime_to_iso(row["arrival"] or 0)
            )
            if payload and self.post_event(payload):
                log.info("Captured notification from %s", app)
