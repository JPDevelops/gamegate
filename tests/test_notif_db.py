"""Tests for reading Windows notifications straight from wpndatabase.db — the
no-packaging capture path. Exercised on Linux against a synthetic database that
mirrors the real schema (Notification + NotificationHandler)."""
import gzip
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import notif_db

TOAST_XML = (
    '<toast><visual><binding template="ToastGeneric">'
    "<text>{title}</text><text>{body}</text>"
    "</binding></visual></toast>"
)


def _make_db(path, rows):
    """rows: list of (handler_primary_id, toast_title, toast_body, arrival_filetime).
    A None title marks a non-toast payload (tile/badge) that must be skipped."""
    conn = sqlite3.connect(path)
    conn.executescript(
        'CREATE TABLE NotificationHandler (RecordId INTEGER PRIMARY KEY, PrimaryId TEXT);'
        'CREATE TABLE Notification ("Order" INTEGER PRIMARY KEY AUTOINCREMENT, '
        "HandlerId INTEGER, Type TEXT, Payload BLOB, ArrivalTime INTEGER);"
    )
    for i, (app, title, body, arrival) in enumerate(rows, start=1):
        conn.execute("INSERT INTO NotificationHandler (RecordId, PrimaryId) VALUES (?, ?)", (i, app))
        if title is None:
            payload = b"\x00\x01binary-tile"          # not toast XML
        else:
            payload = TOAST_XML.format(title=title, body=body).encode("utf-8")
        conn.execute(
            'INSERT INTO Notification (HandlerId, Type, Payload, ArrivalTime) VALUES (?, ?, ?, ?)',
            (i, "toast", payload, arrival),
        )
    conn.commit()
    conn.close()


def test_parse_payload_plain_and_gzip():
    xml = TOAST_XML.format(title="Ping from Alice", body="you there?")
    assert notif_db.parse_payload(xml) == ("Ping from Alice", "you there?")
    assert notif_db.parse_payload(xml.encode()) == ("Ping from Alice", "you there?")
    assert notif_db.parse_payload(gzip.compress(xml.encode())) == ("Ping from Alice", "you there?")
    assert notif_db.parse_payload(b"\x00\x01not xml") == ("", "")
    assert notif_db.parse_payload("") == ("", "")


def test_filetime_conversion_and_fallback():
    # 2021-01-01T00:00:00Z in FILETIME ticks: (1609459200 + 11644473600) * 1e7.
    iso = notif_db.filetime_to_iso(132539328000000000)
    assert iso.startswith("2021-01-01T00:00:00")
    # Garbage/zero -> a valid 'now' timestamp, never a crash.
    assert notif_db.filetime_to_iso(0).endswith("+00:00")
    assert notif_db.filetime_to_iso(None).endswith("+00:00")


def test_reader_emits_only_new_and_skips_self_and_tiles(tmp_path, monkeypatch):
    db = tmp_path / "wpndatabase.db"
    _make_db(db, [
        ("com.squirrel.Discord.Discord", "Alice", "hey", 132537312000000000),
        ("GameGate", "our own toast", "ignore me", 132537312000000000),   # self -> skip
        ("SomeApp", None, None, 132537312000000000),                       # tile -> skip
    ])
    monkeypatch.setattr(notif_db, "db_path", lambda: db)

    posted = []
    reader = notif_db.NotificationDbReader(lambda payload: posted.append(payload) or True)

    # Fresh reader starts at the current tail: a poll now emits NOTHING (no new).
    reader._last_order = reader._max_order()
    reader._poll_once()
    assert posted == []

    # A new Slack notification arrives -> captured; self + tile still skipped.
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO NotificationHandler (RecordId, PrimaryId) VALUES (4, 'Slack')")
    conn.execute(
        'INSERT INTO Notification (HandlerId, Type, Payload, ArrivalTime) VALUES '
        "(4, 'toast', ?, 132537312000000000)",
        (TOAST_XML.format(title="Bob", body="standup?").encode(),),
    )
    conn.commit()
    conn.close()

    reader._poll_once()
    assert len(posted) == 1
    ev = posted[0]
    assert ev["source"] == "slack"
    assert ev["sender"] == "Slack"
    assert ev["title"] == "Bob"
    assert ev["metadata"]["origin"] == "windows-notification"


def test_reader_maps_discord_from_aumid(tmp_path, monkeypatch):
    db = tmp_path / "wpndatabase.db"
    _make_db(db, [("com.squirrel.Discord.Discord", "Carol", "gg?", 132537312000000000)])
    monkeypatch.setattr(notif_db, "db_path", lambda: db)

    posted = []
    reader = notif_db.NotificationDbReader(lambda p: posted.append(p) or True)
    reader._last_order = 0          # capture from the beginning
    reader._poll_once()
    assert len(posted) == 1
    assert posted[0]["source"] == "discord"   # matched from the AUMID substring


def test_reader_no_db_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(notif_db, "db_path", lambda: tmp_path / "nope.db")
    reader = notif_db.NotificationDbReader(lambda p: True)
    assert reader._max_order() == 0
    reader._poll_once()   # must not raise
