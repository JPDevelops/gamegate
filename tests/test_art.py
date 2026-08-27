"""Built-in generated cover art: keyless, deterministic, always returns an SVG."""
from app.api.art import generate_cover_svg


def test_art_returns_svg_for_any_game(client):
    r = client.get("/art", params={"game": "Rust"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in r.text and "RUST" in r.text  # title rendered, uppercased


def test_art_needs_no_key_and_never_404s(client, monkeypatch):
    # No SteamGridDB key, no network — every game still gets a cover.
    monkeypatch.delenv("STEAMGRIDDB_API_KEY", raising=False)
    for game in ("Minecraft", "ObscureIndieGame", "A", ""):
        assert client.get("/art", params={"game": game}).status_code == 200


def test_cover_is_deterministic():
    # Same name → byte-identical SVG (so the WebView can cache it).
    assert generate_cover_svg("Helldivers 2") == generate_cover_svg("Helldivers 2")
    # Different names → different covers (different palette/title).
    assert generate_cover_svg("Rust") != generate_cover_svg("Valheim")


def test_minecraft_gets_its_theme():
    svg = generate_cover_svg("Minecraft")
    assert "MINECRAFT" in svg
    assert "#5a8f34" in svg  # the hand-picked grass-green top color
    assert svg.count("<rect") >= 5  # the block motif adds pixel squares


def test_title_is_escaped_and_bounded():
    # XSS-safe (title is escaped) and a huge name can't blow up the SVG.
    svg = generate_cover_svg('<script>"x" & y')
    assert "<script>" not in svg and "&lt;script&gt;" in svg
    long_svg = generate_cover_svg("Supercalifragilistic Expialidocious Adventure Quest")
    assert long_svg.count("<text") <= 2  # at most two title lines
