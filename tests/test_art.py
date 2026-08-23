"""Universal art endpoint: cache behavior and lookup parsing."""
import httpx

from app.api import art as art_module


def test_art_without_key_is_404(client, monkeypatch):
    monkeypatch.delenv("STEAMGRIDDB_API_KEY", raising=False)
    assert client.get("/art", params={"game": "Rust"}, follow_redirects=False).status_code == 404


def test_art_lookup_redirects_and_caches(client, monkeypatch):
    monkeypatch.setenv("STEAMGRIDDB_API_KEY", "k")
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if "autocomplete" in str(request.url):
            return httpx.Response(200, json={"data": [{"id": 42}]})
        return httpx.Response(200, json={"data": [{"url": "https://cdn.example/rust.png"}]})

    monkeypatch.setattr(
        art_module, "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    first = client.get("/art", params={"game": "Fortnite"}, follow_redirects=False)
    assert first.status_code == 307
    assert first.headers["location"] == "https://cdn.example/rust.png"
    assert calls["n"] == 2

    again = client.get("/art", params={"game": "Fortnite"}, follow_redirects=False)
    assert again.status_code == 307
    assert calls["n"] == 2  # served from cache, SteamGridDB not asked again


def test_art_miss_is_negative_cached(client, monkeypatch):
    monkeypatch.setenv("STEAMGRIDDB_API_KEY", "k")

    def handler(request):
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(
        art_module, "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.get("/art", params={"game": "ObscureGame"}, follow_redirects=False).status_code == 404
    assert client.get("/art", params={"game": "ObscureGame"}, follow_redirects=False).status_code == 404
