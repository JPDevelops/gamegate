"""Settings: typed validation, ingest consumption, transactional clear."""
from tests.test_events import make_event


def test_defaults_and_roundtrip(client):
    s = client.get("/settings").json()
    assert s["urgent_breakthrough"] is True
    assert s["overlay_duration_s"] == 8
    updated = client.put("/settings", json={"overlay_duration_s": 12,
                                            "notification_sound": False}).json()
    assert updated["overlay_duration_s"] == 12
    assert updated["notification_sound"] is False
    assert updated["version"] > s["version"]


def test_validation_rejects_bad_values(client):
    assert client.put("/settings", json={"overlay_duration_s": 99}).status_code == 422
    assert client.put("/settings", json={"urgent_breakthrough": "yes"}).status_code == 422
    assert client.put("/settings", json={"nonsense": 1}).status_code == 422


def test_vip_sender_upgrades_any_source_to_urgent(client):
    client.put("/settings", json={"vip_senders": ["Dad <DAD@Family.com>"]})
    client.post("/status", json={"state": "gaming", "application": "g.exe"})
    client.post("/events", json=make_event(
        external_id="vip1", sender="dad@family.com", priority="informational",
        title="hey", content="call me"))
    pending = client.get("/notifications/pending").json()
    assert len(pending) == 1  # informational became urgent -> broke through
    assert pending[0]["event"]["priority"] == "urgent"


def test_urgent_keyword_upgrades(client):
    client.post("/status", json={"state": "gaming", "application": "g.exe"})
    client.post("/events", json=make_event(
        external_id="kw1", priority="informational", requires_action=False,
        title="prod EMERGENCY right now", content=""))
    assert len(client.get("/notifications/pending").json()) == 1


def test_breakthrough_off_setting_queues_urgent(client):
    client.put("/settings", json={"urgent_breakthrough": False})
    client.post("/status", json={"state": "gaming", "application": "g.exe"})
    client.post("/events", json=make_event(external_id="bt1", priority="urgent"))
    assert client.get("/notifications/pending").json() == []
    assert client.get("/digest").json()["total_events"] == 1


def test_client_settings_shape(client):
    body = client.get("/settings/client").json()
    assert set(body) == {"notification_sound", "overlay_duration_s", "version"}


def test_clear_data_requires_confirmation_and_preserves_settings(client):
    client.put("/settings", json={"overlay_duration_s": 15})
    client.post("/events", json=make_event(external_id="c1"))
    client.post("/status", json={"state": "gaming", "application": "g.exe"})
    client.post("/status", json={"state": "available"})

    assert client.post("/data/clear", json={"confirm": "nope"}).status_code == 422
    cleared = client.post("/data/clear", json={"confirm": "DELETE"}).json()["cleared"]
    assert cleared["events"] >= 1 and cleared["sessions"] >= 1
    assert client.get("/events").json() == []
    assert client.get("/settings").json()["overlay_duration_s"] == 15  # settings survive
    assert client.get("/status").json()["state"] == "available"
