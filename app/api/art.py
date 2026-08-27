"""Built-in game cover art — no API key, no setup, works offline.

GET /art?game=<name> → a generated SVG "cover" for that game (image/svg+xml,
200). Every game gets a clean, distinctive cover derived deterministically from
its name, with hand-picked theming for titles GameGate recognizes (e.g.
Minecraft's grass-green). Steam games get their real header art elsewhere (the
recap uses the keyless Steam CDN when it has a Steam app id); this fills in
everything else so a recap never shows a blank.

Previously this proxied SteamGridDB and needed a per-user API key wired as a
connector — the PO wanted logos built into the app with zero setup instead, so
the art is now generated locally.
"""
import hashlib
import html
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.security import require_api_token

log = logging.getLogger("gamegate.art")

router = APIRouter(dependencies=[Depends(require_api_token)])

W, H = 460, 215  # 2.14:1 — fits both the recap hero background and the list thumb

# Hand-picked palettes (top, bottom, motif) for games we recognize, so their
# cover reads as "that game" at a glance. motif "block" adds a pixel row.
_GAME_THEMES: dict[str, tuple[str, str, str]] = {
    "minecraft": ("#5a8f34", "#24400f", "block"),   # grass green → dirt
}


def _palette(game: str) -> tuple[str, str, str]:
    key = (game or "").strip().lower()
    for name, theme in _GAME_THEMES.items():
        if name in key:
            return theme
    # Deterministic, pleasant palette from the name: a rich dark diagonal so the
    # white title always reads. sha256 (not hash()) so it's stable across runs.
    digest = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16)
    hue = digest % 360
    return f"hsl({hue},52%,32%)", f"hsl({(hue + 38) % 360},58%,15%)", "plain"


def _wrap_title(game: str, max_per_line: int = 12) -> list[str]:
    """Greedy word-wrap to at most two lines so the title fills the cover without
    overflowing. A single very long word is hard-truncated."""
    words = (game or "Game").split()
    lines: list[str] = []
    cur = ""
    for word in words:
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= max_per_line:
            cur += " " + word
        else:
            lines.append(cur)
            cur = word
        if len(lines) == 2:
            break
    if cur and len(lines) < 2:
        lines.append(cur)
    lines = lines[:2] or ["Game"]
    return [(ln[:16] + "…") if len(ln) > 17 else ln for ln in lines]


def generate_cover_svg(game: str) -> str:
    """A self-contained SVG cover for `game`. Pure + deterministic (no network,
    no clock, no randomness) so it's cacheable and testable."""
    top, bottom, motif = _palette(game)
    lines = _wrap_title(game)
    longest = max(len(ln) for ln in lines)
    font_size = 52 if longest <= 8 else 40 if len(lines) == 1 else 34
    total_h = font_size * len(lines) + (6 if len(lines) > 1 else 0)
    y0 = H / 2 - total_h / 2 + font_size * 0.78
    tspans = "".join(
        f'<text x="{W / 2}" y="{y0 + i * (font_size + 6):.0f}" text-anchor="middle" '
        f'font-family="Segoe UI, Arial, sans-serif" font-size="{font_size}" '
        f'font-weight="800" letter-spacing="1.5" fill="#ffffff">{html.escape(ln.upper())}</text>'
        for i, ln in enumerate(lines)
    )
    # A subtle block motif for recognized "block" games; a soft glow otherwise.
    if motif == "block":
        blocks = "".join(
            f'<rect x="{20 + i * 26}" y="{H - 30}" width="20" height="20" rx="2" '
            f'fill="#ffffff" opacity="{0.05 + (i % 3) * 0.03:.2f}"/>'
            for i in range(W // 26)
        )
    else:
        blocks = (f'<circle cx="{W * 0.82}" cy="{H * 0.28}" r="120" fill="#ffffff" '
                  f'opacity="0.05"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(game or "Game")}">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/>'
        f'</linearGradient></defs>'
        f'<rect width="{W}" height="{H}" fill="url(#g)"/>'
        f'{blocks}'
        f'<rect width="{W}" height="{H}" fill="none" stroke="#ffffff" stroke-opacity="0.08"/>'
        f'{tspans}</svg>'
    )


@router.get("/art")
def game_art(game: Annotated[str, Query(max_length=128)] = "") -> Response:
    """A generated cover for the game — always 200, no key, no external calls."""
    svg = generate_cover_svg(game or "Game")
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},  # deterministic per name
    )
