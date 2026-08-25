"""Typed, validated user settings (schema, not loose key-value).

Stored in the settings table as strings; this layer owns types, defaults,
bounds, and a monotonically increasing version so clients apply changes only
when something actually changed.
"""
import json
from email.utils import parseaddr

from app.db import Database

DEFS = {
    "urgent_breakthrough": {"kind": "bool", "default": True},
    "notification_sound": {"kind": "bool", "default": True},
    "overlay_duration_s": {"kind": "int", "default": 8, "min": 4, "max": 20},
    "freshness_minutes": {"kind": "int", "default": 10, "min": 1, "max": 120},
    "vip_senders": {"kind": "list", "default": []},
    "urgent_keywords": {"kind": "list", "default": ["urgent", "asap", "emergency"]},
    # AI classifier: whether to score incoming notifications with an LLM. The
    # key itself is a SECRET, stored separately and NEVER returned to clients
    # (only a boolean "is it set?"), so it can't leak through GET /settings.
    "classifier_enabled": {"kind": "bool", "default": False},
    # Gmail (local IMAP + app password path): whether to poll the inbox. The
    # app password is a SECRET (stored + handled like the classifier key); the
    # address is the user's own email and is safe to return so the UI can show
    # "connected as …".
    "gmail_enabled": {"kind": "bool", "default": False},
    # Text messages: whether the user has set up phone-text sync (via Windows
    # Phone Link). There's no credential — texts arrive as captured Windows
    # notifications; this flag just drives the connector's state + the setup
    # walkthrough. Turning it off is presentational (capture itself is global).
    "text_sync_enabled": {"kind": "bool", "default": False},
}
VERSION_KEY = "_version"
CLASSIFIER_KEY_NAME = "classifier_api_key"  # secret; not in DEFS, never returned
GMAIL_ADDRESS_KEY_NAME = "gmail_address"          # user's own email (returnable)
GMAIL_PASSWORD_KEY_NAME = "gmail_app_password"    # secret; never returned


def normalize_sender(sender: str) -> str:
    """'Boss Man <Boss@Work.com>' -> 'boss@work.com' (deterministic identity)."""
    address = parseaddr(sender or "")[1] or (sender or "")
    return address.strip().lower()


class SettingsService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get_all(self) -> dict:
        rows = dict(
            self.db.connection().execute("SELECT key, value FROM settings").fetchall()
        )
        out = {}
        for key, definition in DEFS.items():
            if key in rows:
                out[key] = self._decode(definition, rows[key])
            else:
                out[key] = definition["default"]
        out["version"] = int(rows.get(VERSION_KEY, "0"))
        # Expose only WHETHER the AI key is set — never the key itself.
        out["classifier_api_key_set"] = bool(self._decode_secret(rows.get(CLASSIFIER_KEY_NAME)))
        # Gmail: return the address (the user's own email, for the UI) but only a
        # boolean for the app password — the secret itself never leaves the server.
        out["gmail_address"] = self._decode_secret(rows.get(GMAIL_ADDRESS_KEY_NAME))
        out["gmail_app_password_set"] = bool(self._decode_secret(rows.get(GMAIL_PASSWORD_KEY_NAME)))
        return out

    def get_classifier_key(self) -> str:
        """The stored AI API key (server-side use only — never send to a client)."""
        row = self.db.connection().execute(
            "SELECT value FROM settings WHERE key = ?", (CLASSIFIER_KEY_NAME,)
        ).fetchone()
        return self._decode_secret(row[0]) if row else ""

    def set_classifier_key(self, value: str) -> None:
        """Store (or clear) the AI API key and bump the settings version."""
        conn = self.db.connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (CLASSIFIER_KEY_NAME, json.dumps((value or "").strip())),
            )
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, '1')"
                " ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1",
                (VERSION_KEY,),
            )

    def get_gmail_credentials(self) -> tuple[str, str]:
        """(address, app_password) for server-side IMAP use only — the app
        password is NEVER sent to a client."""
        rows = dict(
            self.db.connection()
            .execute(
                "SELECT key, value FROM settings WHERE key IN (?, ?)",
                (GMAIL_ADDRESS_KEY_NAME, GMAIL_PASSWORD_KEY_NAME),
            )
            .fetchall()
        )
        return (
            self._decode_secret(rows.get(GMAIL_ADDRESS_KEY_NAME)),
            self._decode_secret(rows.get(GMAIL_PASSWORD_KEY_NAME)),
        )

    def set_gmail_credentials(self, address: str, app_password: str) -> None:
        """Store (or clear) the Gmail address + app password and bump the version.
        Pass empty strings to clear both."""
        conn = self.db.connection()
        with conn:
            for key, value in (
                (GMAIL_ADDRESS_KEY_NAME, (address or "").strip()),
                (GMAIL_PASSWORD_KEY_NAME, (app_password or "").strip()),
            ):
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, json.dumps(value)),
                )
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, '1')"
                " ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1",
                (VERSION_KEY,),
            )

    @staticmethod
    def _decode_secret(raw) -> str:
        if not raw:
            return ""
        try:
            return json.loads(raw) or ""
        except (json.JSONDecodeError, TypeError):
            return ""

    def update(self, changes: dict) -> dict:
        validated = {}
        for key, value in changes.items():
            if key not in DEFS:
                raise ValueError(f"Unknown setting {key!r}")
            validated[key] = self._validate(key, value)
        # Only persist and bump the version for values that ACTUALLY differ from
        # what's stored (review MINOR #16): the version is a client cache-buster,
        # so a PUT of {} or of the current values must be a no-op, not a bump.
        current = self.get_all()
        actually_changed = {
            key: value for key, value in validated.items() if current.get(key) != value
        }
        if not actually_changed:
            return current
        conn = self.db.connection()
        with conn:
            for key, value in actually_changed.items():
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, self._encode(DEFS[key], value)),
                )
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, '1')"
                " ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1",
                (VERSION_KEY,),
            )
        return self.get_all()

    def _validate(self, key: str, value):
        definition = DEFS[key]
        kind = definition["kind"]
        if kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true/false")
            return value
        if kind == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{key} must be a number")
            if not definition["min"] <= value <= definition["max"]:
                raise ValueError(
                    f"{key} must be between {definition['min']} and {definition['max']}"
                )
            return value
        if kind == "list":
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError(f"{key} must be a list of strings")
            if len(value) > 200:
                raise ValueError(f"{key} is limited to 200 entries")
            if any(len(x) > 200 for x in value):
                raise ValueError(f"{key} entries are limited to 200 characters")
            cleaned = [x.strip() for x in value if x.strip()]
            if key == "vip_senders":
                cleaned = [normalize_sender(x) for x in cleaned]
            else:
                cleaned = [x.lower() for x in cleaned]
            return cleaned
        raise ValueError(f"Unhandled kind {kind}")

    @staticmethod
    def _encode(definition, value) -> str:
        return json.dumps(value)

    @staticmethod
    def _decode(definition, raw: str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return definition["default"]
