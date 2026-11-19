"""Read-only access to the Postiz public API: which social accounts exist.

Fork feature (docs/live-clipping.md#posting). Posting itself is done by the
live recorder; the backend only lists accounts so a user can authorize them.
POSTIZ_URL is the Postiz base URL, POSTIZ_API_KEY its public API key.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

# Providers the live recorder knows how to post a vertical video to.
SUPPORTED_PROVIDERS = frozenset({"tiktok", "youtube", "instagram", "instagram-standalone"})


class PostizNotConfigured(RuntimeError):
    pass


def _settings() -> tuple[str, str]:
    url = os.getenv("POSTIZ_URL", "").strip().rstrip("/")
    key = os.getenv("POSTIZ_API_KEY", "").strip()
    if not url or not key:
        raise PostizNotConfigured("Set POSTIZ_URL and POSTIZ_API_KEY to post live clips")
    return url, key


def allowed_emails() -> frozenset[str]:
    """SupoClip accounts that may use this server's Postiz (one key for the whole server)."""
    raw = os.getenv("POSTIZ_ALLOWED_EMAILS", "")
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def configured() -> bool:
    try:
        _settings()
    except PostizNotConfigured:
        return False
    return True


async def list_integrations() -> list[dict[str, Any]]:
    """Connected accounts: id, identifier (provider), name, picture, disabled."""
    url, key = _settings()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{url}/api/public/v1/integrations", headers={"Authorization": key})
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise RuntimeError("Unexpected Postiz integrations response")
    return [
        {
            "id": str(item.get("id")),
            "provider": str(item.get("identifier") or ""),
            "name": str(item.get("name") or ""),
            "picture": item.get("picture"),
            "disabled": bool(item.get("disabled")),
        }
        for item in data
        if item.get("id")
    ]
