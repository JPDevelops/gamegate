"""Silence-duplicate-pop-ups: mute the native banner for GameGate's messaging
apps while keeping them captured. The registry writes are Windows-only; here we
test the pure matching logic + that the endpoint/toggle behave safely off-Windows
(where apply() is a no-op)."""
from app.services import notification_banners as nb


def test_is_messaging_app_matches_gamegates_sources_only():
    # Real AUMIDs GameGate surfaces → muted.
    assert nb.is_messaging_app("com.squirrel.Discord.Discord")
    assert nb.is_messaging_app("Microsoft.YourPhone_8wekyb3d8bbwe!YourPhoneMessages")
    assert nb.is_messaging_app("Microsoft.Teams_8wekyb3d8bbwe!App")
    # Everything else keeps its normal banners.
    assert not nb.is_messaging_app("Microsoft.GamingApp_8wekyb3d8bbwe!Xbox")
    assert not nb.is_messaging_app("Microsoft.ScreenSketch_8wekyb3d8bbwe!App")
    assert not nb.is_messaging_app("Chrome")   # browsers excluded on purpose
    assert not nb.is_messaging_app("")


def test_apply_is_a_safe_noop_off_windows():
    # No registry off-Windows; must never raise, just report nothing touched.
    assert nb.apply(True) == []
    assert nb.apply(False) == []


def test_endpoint_persists_and_reports(client):
    # Default off.
    assert client.get("/settings").json()["suppress_source_banners"] is False
    r = client.post("/settings/notifications-suppress", json={"enabled": True})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["affected"] == []   # [] off-Windows
    assert client.get("/settings").json()["suppress_source_banners"] is True
    # Toggling back off persists too.
    assert client.post("/settings/notifications-suppress", json={"enabled": False}).json()["enabled"] is False
    assert client.get("/settings").json()["suppress_source_banners"] is False
