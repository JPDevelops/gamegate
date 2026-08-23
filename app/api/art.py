"""Game artwork for non-Steam titles via SteamGridDB (Jules' option B).

GET /art?game=<name> → 307 redirect to a cached artwork URL, or 404 when no
art exists. Results (including misses) are cached in SQLite so SteamGridDB
is asked at most once per game name.
"""
import logging
import os
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app.db import get_database
from app.security import require_api_token

log = logging.getLogger("gamegate.art")

router = APIRouter(dependencies=[Depends(require_api_token)])

API_BASE = "https://www.steamgriddb.com/api/v2"


def _client() -> httpx.Client:
    return httpx.Client(timeout=8)


def lookup_art(game: str, client: httpx.Client, api_key: str) -> str:
    """One-shot SteamGridDB lookup: search the name, take the top match's
    best 460x215 grid. Returns '' when nothing suitable exists."""
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        search = client.get(
            f"{API_BASE}/search/autocomplete/{quote(game)}", headers=headers
        )
        search.raise_for_status()
        results = search.json().get("data", [])
        if not results:
            return ""
        grids = client.get(
            f"{API_BASE}/grids/game/{results[0]['id']}?dimensions=460x215",
            headers=headers,
        )
        grids.raise_for_status()
        data = grids.json().get("data", [])
        return data[0]["url"] if data else ""
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.warning("SteamGridDB lookup failed for %r: %s", game, exc)
        raise HTTPException(status_code=502, detail="art lookup failed") from exc


@router.get("/art")
def game_art(game: str) -> RedirectResponse:
    api_key = os.environ.get("STEAMGRIDDB_API_KEY", "")
    if not game or not api_key:
        raise HTTPException(status_code=404, detail="no art")

    conn = get_database().connection()
    row = conn.execute("SELECT url FROM art_cache WHERE game = ?", (game,)).fetchone()
    if row is not None:
        if not row["url"]:
            raise HTTPException(status_code=404, detail="no art")
        return RedirectResponse(row["url"], status_code=307)

    url = lookup_art(game, _client(), api_key)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO art_cache (game, url, fetched_at) VALUES (?, ?, ?)",
            (game, url, datetime.now(UTC).isoformat()),
        )
    if not url:
        raise HTTPException(status_code=404, detail="no art")
    return RedirectResponse(url, status_code=307)
