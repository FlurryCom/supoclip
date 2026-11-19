"""Trending live channels on Twitch (fork feature), through the official Helix API.

Uses an app access token (client credentials: TWITCH_CLIENT_ID and
TWITCH_CLIENT_SECRET from dev.twitch.tv), so no Twitch user ever signs in. The
token and the game ids are cached in memory.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
HELIX = "https://api.twitch.tv/helix"
GAMES = {"gta6": "Grand Theft Auto VI"}  # filter key -> Twitch category name
LANGUAGES = {"en", "pt", "es"}
_cache: dict[str, Any] = {}


class TwitchNotConfigured(Exception):
    """TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET are not set."""


def _keys() -> tuple[str, str]:
    client_id, secret = os.getenv("TWITCH_CLIENT_ID", "").strip(), os.getenv("TWITCH_CLIENT_SECRET", "").strip()
    if not client_id or not secret:
        raise TwitchNotConfigured("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET are not set")
    return client_id, secret


async def _token(http, client_id: str, secret: str) -> str:
    cached = _cache.get("token")
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    response = await http.post(TOKEN_URL, params={
        "client_id": client_id, "client_secret": secret, "grant_type": "client_credentials"}, timeout=15)
    response.raise_for_status()
    data = response.json()
    _cache["token"] = (data["access_token"], time.time() + int(data.get("expires_in", 3600)))
    return data["access_token"]


async def _game_id(http, headers: dict, name: str) -> str | None:
    key = f"game:{name}"
    if key not in _cache:
        response = await http.get(f"{HELIX}/games", params={"name": name}, headers=headers, timeout=15)
        response.raise_for_status()
        found = response.json().get("data") or []
        _cache[key] = found[0]["id"] if found else None
    return _cache[key]


def trending_rows(helix: dict[str, Any], monitored: set[str]) -> list[dict[str, Any]]:
    rows = [
        {"login": s["user_login"].lower(), "name": s["user_name"], "viewers": int(s["viewer_count"]),
         "language": s.get("language"), "game": s.get("game_name"), "title": s.get("title"),
         "monitored": s["user_login"].lower() in monitored}
        for s in helix.get("data") or []
    ]
    return sorted(rows, key=lambda r: r["viewers"], reverse=True)


async def top_streams(game: str, language: str, monitored: set[str] | None = None, http=None) -> list[dict[str, Any]]:
    """The 20 most watched live streams, optionally in one game and one language."""
    client_id, secret = _keys()
    own = http is None
    http = http or httpx.AsyncClient()
    try:
        headers = {"Client-Id": client_id, "Authorization": f"Bearer {await _token(http, client_id, secret)}"}
        params: dict[str, Any] = {"first": 20}
        if game in GAMES:
            game_id = await _game_id(http, headers, GAMES[game])
            if game_id:
                params["game_id"] = game_id
        if language in LANGUAGES:
            params["language"] = language
        response = await http.get(f"{HELIX}/streams", params=params, headers=headers, timeout=15)
        response.raise_for_status()
        return trending_rows(response.json(), monitored or set())
    finally:
        if own:
            await http.aclose()
