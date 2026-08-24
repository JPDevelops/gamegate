from app import __version__


def test_health_returns_ok_and_version(client):
    # Use the shared `client` fixture (fresh temp DB, isolated env, lifespan
    # managed) rather than a module-level TestClient (review NITPICK #19).
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_ready_touches_the_database(client):
    """/ready actually reads SQLite, so it proves more than /health's always-ok
    liveness (review MINOR)."""
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
