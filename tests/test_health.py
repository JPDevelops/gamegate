from app import __version__


def test_health_returns_ok_and_version(client):
    # Use the shared `client` fixture (fresh temp DB, isolated env, lifespan
    # managed) rather than a module-level TestClient (review NITPICK #19).
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}
