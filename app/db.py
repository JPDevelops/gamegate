"""SQLite connection and schema management.

One Database object per process, initialized at startup (or per-test with a
temporary path). Repositories receive it explicitly — no hidden globals in the
data layer itself.
"""
import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL,
    priority TEXT NOT NULL,
    requires_action INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    digest_id TEXT,
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL,
    application TEXT,
    started_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    application TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds INTEGER
);

CREATE TABLE IF NOT EXISTS digests (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS art_cache (
    game TEXT PRIMARY KEY,
    url TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id),
    created_at TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._local = threading.local()
        with self.connection() as conn:  # creates the file
            pass
        # The DB holds message content — lock it to the owner (0600). It was
        # created world-readable by default umask (review M9). Best-effort:
        # in-memory / unusual paths may not support chmod.
        try:
            if os.path.exists(path):
                os.chmod(path, 0o600)
        except OSError:
            pass
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            # Lightweight migration: columns added after first release. Only
            # swallow the specific "duplicate column" case (already applied) —
            # a locked db or I/O error must still surface (N29).
            for migration in (
                "ALTER TABLE sessions ADD COLUMN app_id TEXT",
                "ALTER TABLE events ADD COLUMN read_at TEXT",
                # Manual DND override, distinct from the detector-driven `state`
                # (dashboard-authoritative DND): NULL = no override, else the
                # override IS the effective state and the detector can't clobber it.
                "ALTER TABLE status ADD COLUMN override_state TEXT",
                # Dead-letter marker: a delivery the client gave up on is marked
                # delivered with failed_at set, so it leaves the pending queue and
                # can't wedge newer items behind it (review MAJOR: poison starve).
                "ALTER TABLE notifications ADD COLUMN failed_at TEXT",
                "ALTER TABLE digests ADD COLUMN failed_at TEXT",
            ):
                try:
                    conn.execute(migration)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
            # At most one digest per session — enforced by the DB, not just by
            # code, so a retry/race can never create a second recap (review M2).
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_digests_session"
                " ON digests(session_id) WHERE session_id IS NOT NULL"
            )

    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            # WAL + busy timeout: concurrent connector writes wait briefly
            # instead of failing with "database is locked".
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            self._local.conn = conn
        return conn


_database: Database | None = None


def init_database(path: str) -> Database:
    global _database
    _database = Database(path)
    return _database


def get_database() -> Database:
    if _database is None:
        raise RuntimeError("Database not initialized — call init_database() first")
    return _database
