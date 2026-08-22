import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.status_store import status_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_store():
    status_store.reset()


def test_initial_state_is_available():
    response = client.get("/status")
    assert response.status_code == 200
    assert response.json()["state"] == "available"


def test_post_gaming_status_and_read_back():
    payload = {
        "state": "gaming",
        "application": "helldivers2.exe",
        "started_at": "2026-08-22T20:31:00-07:00",
    }
    post = client.post("/status", json=payload)
    assert post.status_code == 200
    assert post.json()["state"] == "gaming"
    assert post.json()["application"] == "helldivers2.exe"

    get = client.get("/status")
    assert get.json()["state"] == "gaming"


def test_invalid_state_is_rejected():
    response = client.post("/status", json={"state": "napping"})
    assert response.status_code == 422

    # and the bad request must not have changed the stored state
    assert client.get("/status").json()["state"] == "available"


def test_state_only_update_clears_application():
    client.post("/status", json={"state": "gaming", "application": "helldivers2.exe"})
    client.post("/status", json={"state": "available"})
    body = client.get("/status").json()
    assert body["state"] == "available"
    assert body["application"] is None
