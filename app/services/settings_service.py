"""Typed, validated user settings (Orion: schema, not loose key-value).

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
}
VERSION_KEY = "_version"


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
        return out

    def update(self, changes: dict) -> dict:
        validated = {}
        for key, value in changes.items():
            if key not in DEFS:
                raise ValueError(f"Unknown setting {key!r}")
            validated[key] = self._validate(key, value)
        conn = self.db.connection()
        with conn:
            for key, value in validated.items():
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
