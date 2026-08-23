"""SQLite connection and schema management.

One Database object per process, initialized at startup (or per-test with a
temporary path). Repositories receive it explicitly — no hidden globals in the
data layer itself.
"""
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
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            # Lightweight migration: columns added after first release.
            for migration in (
                "ALTER TABLE sessions ADD COLUMN app_id TEXT",
                "ALTER TABLE events ADD COLUMN read_at TEXT",
            ):
                try:
                    conn.execute(migration)
                except sqlite3.OperationalError:
                    pass  # already present

    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            # WAL + busy timeout: concurrent connector writes wait briefly
            # instead of failing with "database is locked" (Vega audit #7).
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
