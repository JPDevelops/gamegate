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
