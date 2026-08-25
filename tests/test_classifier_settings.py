"""AI-classifier settings + auto-classification on ingest (no live LLM calls)."""
from datetime import UTC, datetime

from app import db as db_module
from app.config import Settings
from app.models.event import EventIn, EventPriority, EventSource
from app.services import classifier as clsmod
from app.services.ingest_service import IngestService
from app.services.settings_service import SettingsService


def test_set_classifier_stores_key_as_secret(client):
    r = client.post("/settings/classifier", json={"enabled": True, "api_key": "sk-secret-123"})
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "api_key_set": True}
    # The raw key must NEVER come back through the settings read.
    s = client.get("/settings").json()
    assert s["classifier_enabled"] is True
    assert s["classifier_api_key_set"] is True
    assert "sk-secret-123" not in str(s)


def test_set_classifier_can_clear_key(client):
    client.post("/settings/classifier", json={"enabled": True, "api_key": "sk-x"})
    r = client.post("/settings/classifier", json={"enabled": False, "api_key": ""})
    assert r.json() == {"enabled": False, "api_key_set": False}


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
