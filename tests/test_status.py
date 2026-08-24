def test_initial_state_is_available(client):
    response = client.get("/status")
    assert response.status_code == 200
    assert response.json()["state"] == "available"


def test_post_gaming_status_and_read_back(client):
    payload = {
        "state": "gaming",
        "application": "helldivers2.exe",
        "started_at": "2026-08-22T20:31:00-07:00",
    }
    post = client.post("/status", json=payload)
    assert post.status_code == 200
    assert post.json()["state"] == "gaming"
    assert post.json()["application"] == "helldivers2.exe"
    assert client.get("/status").json()["state"] == "gaming"


def test_invalid_state_is_rejected(client):
    response = client.post("/status", json={"state": "napping"})
    assert response.status_code == 422
    assert client.get("/status").json()["state"] == "available"


def test_state_only_update_clears_application(client):
    client.post("/status", json={"state": "gaming", "application": "helldivers2.exe"})
    client.post("/status", json={"state": "available"})
    body = client.get("/status").json()
    assert body["state"] == "available"
    assert body["application"] is None


def test_switching_games_mid_session_makes_a_separate_recap(client):
    """Quit one game and start another without going available: each game gets
    its own session and digest, not one merged recap (M7)."""
    client.post("/status", json={"state": "gaming", "application": "helldivers2.exe"})
    client.post("/status", json={"state": "gaming", "application": "fortnite.exe"})  # switch
    client.post("/status", json={"state": "available"})
    digests = client.get("/digests").json()
    assert len(digests) == 2  # one recap per game, not a single merged one
