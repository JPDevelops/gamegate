"""AI-classifier settings + auto-classification on ingest (no live LLM calls)."""
from datetime import UTC, datetime

from app import db as db_module
from app.config import Settings
from app.models.event import EventIn, EventPriority, EventSource
from app.services import classifier as clsmod
from app.services.ingest_service import IngestService
from app.services.settings_service import SettingsService


def _accept_key(monkeypatch):
    """Stub the OpenAI key check so tests don't hit the network."""
    from app.api import settings as settings_api
    monkeypatch.setattr(settings_api, "verify_openai_key", lambda k: (True, "Key verified."))


def test_set_classifier_stores_key_as_secret(client, monkeypatch):
    _accept_key(monkeypatch)
    r = client.post("/settings/classifier", json={"enabled": True, "api_key": "sk-secret-123"})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["api_key_set"] is True
    # The raw key must NEVER come back through the settings read.
    s = client.get("/settings").json()
    assert s["classifier_enabled"] is True
    assert s["classifier_api_key_set"] is True
    assert "sk-secret-123" not in str(s)


def test_set_classifier_can_clear_key(client, monkeypatch):
    _accept_key(monkeypatch)
    client.post("/settings/classifier", json={"enabled": True, "api_key": "sk-x"})
    r = client.post("/settings/classifier", json={"enabled": False, "api_key": ""})
    assert r.json()["api_key_set"] is False and r.json()["enabled"] is False


def test_bad_key_is_rejected_and_not_stored(client, monkeypatch):
    from app.api import settings as settings_api
    monkeypatch.setattr(settings_api, "verify_openai_key",
                        lambda k: (False, "OpenAI rejected that key (invalid or revoked)."))
    r = client.post("/settings/classifier", json={"enabled": True, "api_key": "sk-bad"})
    assert r.status_code == 400
    assert "rejected" in r.json()["detail"].lower()
    # A rejected key must NOT be saved or enabled.
    s = client.get("/settings").json()
    assert s["classifier_api_key_set"] is False
    assert s["classifier_enabled"] is False


def test_verify_openai_key_paths():
    import httpx

    from app.services.classifier import verify_openai_key

    def _client(status):
        return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status)))

    assert verify_openai_key("sk-x", client=_client(200))[0] is True
    assert verify_openai_key("sk-x", client=_client(429))[0] is True   # rate-limited but valid
    assert verify_openai_key("sk-x", client=_client(401))[0] is False  # rejected
    assert verify_openai_key("", client=_client(200))[0] is False      # empty key

    def _boom(req):
        raise httpx.ConnectError("offline")
    ok, msg = verify_openai_key("sk-x", client=httpx.Client(transport=httpx.MockTransport(_boom)))
    assert ok is True and "couldn't" in msg.lower()                    # offline = soft-accept


def _event(**over):
    base = {
        "source": EventSource.DISCORD, "external_id": "a1", "sender": "Discord",
        "title": "ping", "content": "you around?", "received_at": datetime.now(UTC),
        "priority": EventPriority.INFORMATIONAL,
    }
    base.update(over)
    return EventIn(**base)


def test_ingest_ai_upgrades_priority_and_adds_summary(tmp_path, monkeypatch):
    db = db_module.init_database(str(tmp_path / "ai.db"))
    SettingsService(db).update({"classifier_enabled": True})

    class _Stub:
        def classify(self, _event):
            return clsmod.Classification(
                category="action_required", urgency=9,
                summary="Boss needs you", suggested_action="Reply now", reason="urgent ask",
            )

    monkeypatch.setattr(
        clsmod, "build_classifier",
        lambda: clsmod.SafeClassifier(_Stub(), clsmod.DeterministicClassifier()),
    )
    event, created, _decision = IngestService(db, Settings()).ingest(_event())
    assert created
    assert event.priority == EventPriority.URGENT           # AI upgraded it
    assert event.metadata["ai"]["summary"] == "Boss needs you"
    assert event.metadata["ai"]["urgency"] == 9


def test_ingest_skips_ai_when_disabled(tmp_path, monkeypatch):
    db = db_module.init_database(str(tmp_path / "off.db"))
    # classifier_enabled defaults False; build_classifier must never be called.
    monkeypatch.setattr(
        clsmod, "build_classifier",
        lambda: (_ for _ in ()).throw(AssertionError("classifier ran while disabled")),
    )
    event, _created, _decision = IngestService(db, Settings()).ingest(_event())
    assert event.priority == EventPriority.INFORMATIONAL
    assert "ai" not in event.metadata


def test_mark_not_urgent_downgrades_and_teaches(client):
    """Marking a message not-urgent drops it AND remembers the sender so future
    messages from them are held — even when a keyword would flag them urgent."""
    from datetime import UTC, datetime
    now = datetime.now(UTC).isoformat()
    ev = client.post("/events", json={
        "source": "text", "external_id": "b1", "sender": "Text", "title": "Blink",
        "content": "Motion detected", "received_at": now, "priority": "urgent",
    }).json()
    r = client.post(f"/events/{ev['id']}/mark", json={"urgent": False})
    assert r.status_code == 200 and r.json()["learned"] == "blink"
    # The sender is now on the never-urgent list...
    assert "blink" in client.get("/settings").json()["never_urgent_senders"]
    # ...so a NEW Blink message with an urgent keyword is still held (informational).
    ev2 = client.post("/events", json={
        "source": "text", "external_id": "b2", "sender": "Text", "title": "Blink",
        "content": "URGENT motion", "received_at": now, "priority": "informational",
    }).json()
    assert ev2["priority"] == "informational"   # mark beats the urgent keyword


def test_mark_urgent_adds_vip_and_breaks_through(client):
    from datetime import UTC, datetime
    now = datetime.now(UTC).isoformat()
    ev = client.post("/events", json={
        "source": "gmail", "external_id": "g1", "sender": "Boss <boss@work.com>",
        "title": "hi", "content": "call me", "received_at": now, "priority": "informational",
    }).json()
    r = client.post(f"/events/{ev['id']}/mark", json={"urgent": True})
    assert r.json()["learned"] == "boss@work.com"
    assert "boss@work.com" in client.get("/settings").json()["vip_senders"]
    # A new message from that sender now comes in urgent.
    ev2 = client.post("/events", json={
        "source": "gmail", "external_id": "g2", "sender": "Boss <boss@work.com>",
        "title": "yo", "content": "later", "received_at": now, "priority": "informational",
    }).json()
    assert ev2["priority"] == "urgent"


def test_message_identity_picks_the_right_who():
    from app.services.ingest_service import message_identity
    assert message_identity("text", "Text", "Blink") == "blink"
    assert message_identity("gmail", "Boss <boss@work.com>", "subject") == "boss@work.com"


def test_silence_holds_the_app_but_still_captures(client):
    """The per-message 'Silence' bell (v0.5.17): the app's messages are still
    CAPTURED (kept for the inbox/recap) but never break through with an overlay —
    a hard stop independent of priority, unlike 'not urgent' which only downgrades.
    Reversible."""
    from datetime import UTC, datetime
    client.post("/status", json={"state": "available"})
    now = datetime.now(UTC).isoformat()
    ev = client.post("/events", json={
        "source": "text", "external_id": "sil1", "sender": "Text", "title": "Blink",
        "content": "Motion detected", "received_at": now, "priority": "informational",
    }).json()
    r = client.post(f"/events/{ev['id']}/silence", json={"silenced": True})
    assert r.status_code == 200 and r.json()["app"] == "blink"
    assert "blink" in client.get("/settings").json()["muted_sources"]

    # A NEW *urgent* Blink is still captured but HELD — never pops — even though
    # urgent + available would normally break through. Priority is left intact
    # (silence isn't a downgrade; it just refuses to interrupt).
    ev2 = client.post("/events", json={
        "source": "text", "external_id": "sil2", "sender": "Text", "title": "Blink",
        "content": "Motion detected", "received_at": now, "priority": "urgent",
    }).json()
    assert ev2["priority"] == "urgent"
    pending = client.get("/notifications/pending").json()
    assert not any(n["event"]["external_id"] == "sil2" for n in pending)  # never popped

    # Control: an un-silenced urgent from a different app DOES break through.
    client.post("/events", json={
        "source": "text", "external_id": "sil3", "sender": "Text", "title": "Mom",
        "content": "call me", "received_at": now, "priority": "urgent",
    })
    pending = client.get("/notifications/pending").json()
    assert any(n["event"]["external_id"] == "sil3" for n in pending)

    # Unsilence removes the rule.
    r2 = client.post(f"/events/{ev['id']}/silence", json={"silenced": False})
    assert r2.status_code == 200
    assert "blink" not in client.get("/settings").json()["muted_sources"]


def test_silence_clears_already_queued_backlog(client):
    """Live bug (2026-08-27): silencing must ALSO drop that source's notifications
    that were queued BEFORE the silence, or the backlog keeps popping overlays
    after the user hit silence."""
    from datetime import UTC, datetime
    client.post("/status", json={"state": "available"})
    now = datetime.now(UTC).isoformat()
    # Two Blink motions break through and QUEUE as pending overlays (pre-silence).
    for i in (1, 2):
        client.post("/events", json={
            "source": "text", "external_id": f"bk{i}", "sender": "Text", "title": "Blink",
            "content": "Motion detected", "received_at": now, "priority": "urgent",
        })
    pending = client.get("/notifications/pending").json()
    blink_pending = [n for n in pending if n["event"]["title"] == "Blink"]
    assert len(blink_pending) == 2  # both are queued and would pop

    # Silence Blink from one of them → the backlog is cleared, not just future ones.
    blink_ev = next(e for e in client.get("/events?limit=100").json() if e["title"] == "Blink")
    r = client.post(f"/events/{blink_ev['id']}/silence", json={"silenced": True})
    assert r.status_code == 200 and r.json()["dismissed"] == 2

    # Pending queue no longer has Blink — nothing left to pop.
    pending2 = client.get("/notifications/pending").json()
    assert not any(n["event"]["title"] == "Blink" for n in pending2)
