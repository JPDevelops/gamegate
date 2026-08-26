"""Silence all pop-ups: mute the native banner for every app (except GameGate)
so GameGate is the single surface, while everything stays captured. The registry
writes are Windows-only; the release test gate runs on Windows, so these mock the
registry (never touch the real one) and force the platform for determinism."""
from app.services import notification_banners as nb


def test_should_mute_covers_everything_except_gamegate():
    # Zero pop-ups: every app is muted...
    assert nb.should_mute("com.squirrel.Discord.Discord")
    assert nb.should_mute(
        "Microsoft.YourPhone_8wekyb3d8bbwe!YourPhoneNotifications_com.immediasemi.android.blink")
    assert nb.should_mute("Microsoft.WindowsStore_8wekyb3d8bbwe!App")
    # ...except GameGate's own (never mute ourselves).
    assert not nb.should_mute("GameGate")
    assert not nb.should_mute("com.jpdevelops.gamegate")
    assert not nb.should_mute("")


def test_apply_is_a_noop_off_windows(monkeypatch):
    monkeypatch.setattr(nb.sys, "platform", "linux")
    assert nb.apply(True) == []
    assert nb.apply(False) == []


def test_apply_mutes_every_app_but_gamegate(monkeypatch):
    """On Windows: mute ShowBanner=0 for all registered apps except GameGate,
    without ever touching the real registry (mocked)."""
    monkeypatch.setattr(nb.sys, "platform", "win32")
    monkeypatch.setattr(nb, "_iter_registered_apps", lambda: iter([
        "com.squirrel.Discord.Discord",
        "Microsoft.YourPhone_8wekyb3d8bbwe!YourPhoneNotifications_com.immediasemi.android.blink",
        "GameGate",
    ]))
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(nb, "_set_banner", lambda aumid, show: calls.append((aumid, show)))

    touched = nb.apply(True)   # silence
    assert "GameGate" not in touched                       # never mute ourselves
    assert "com.squirrel.Discord.Discord" in touched
    assert any("blink" in a for a in touched)              # phone-mirrored apps too
    assert calls and all(show is False for _, show in calls)  # ShowBanner=0

    calls.clear()
    nb.apply(False)            # restore
    assert calls and all(show is True for _, show in calls)   # ShowBanner=1


def test_endpoint_persists_and_reports(client, monkeypatch):
    # Mock the registry apply so the endpoint test never touches the real one and
    # is deterministic on Windows CI.
    monkeypatch.setattr(nb, "apply", lambda enabled: ["FakeApp"] if enabled else [])
    assert client.get("/settings").json()["suppress_source_banners"] is False

    r = client.post("/settings/notifications-suppress", json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["enabled"] is True and r.json()["affected"] == ["FakeApp"]
    assert client.get("/settings").json()["suppress_source_banners"] is True

    off = client.post("/settings/notifications-suppress", json={"enabled": False})
    assert off.json()["enabled"] is False and off.json()["affected"] == []
    assert client.get("/settings").json()["suppress_source_banners"] is False
