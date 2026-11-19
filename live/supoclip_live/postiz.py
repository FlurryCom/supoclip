"""Posting kept live clips to social accounts through the Postiz public API.

Safety first: a clip goes only to accounts the user authorized in SupoClip, and
only while the Postiz integration id still has the provider and name it had
when authorized. Anything else is refused and recorded, never retried
somewhere else.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import httpx


class Postiz:
    def __init__(self, url: str, key: str):
        self.base = url.rstrip("/") + "/api/public/v1"
        self.headers = {"Authorization": key}
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(30, read=600, write=600))

    async def integrations(self) -> list[dict]:
        response = await self.http.get(f"{self.base}/integrations", headers=self.headers)
        response.raise_for_status()
        return [
            {"id": str(i.get("id")), "provider": str(i.get("identifier") or ""), "name": str(i.get("name") or ""),
             "disabled": bool(i.get("disabled"))}
            for i in response.json() if i.get("id")
        ]

    async def upload(self, path: Path) -> dict:
        with path.open("rb") as video:
            response = await self.http.post(
                f"{self.base}/upload", headers=self.headers, files={"file": (path.name, video, "video/mp4")},
            )
        response.raise_for_status()
        media = response.json()
        if not media.get("id") or not media.get("path"):
            raise RuntimeError("Postiz upload returned no media id/path")
        return media

    async def create_post(self, body: dict) -> str:
        response = await self.http.post(f"{self.base}/posts", headers=self.headers, json=body)
        response.raise_for_status()
        data = response.json()
        first = data[0] if isinstance(data, list) and data else data
        return str((first or {}).get("postId") or (first or {}).get("id") or "")

    async def close(self) -> None:
        await self.http.aclose()


def refusal_reason(target: dict, integrations: list[dict]) -> str | None:
    """Why this authorized target must not receive a post right now, if anything."""
    account = next((i for i in integrations if i["id"] == target["integration_id"]), None)
    if account is None:
        return "account is no longer connected in Postiz"
    if account["provider"] != target["provider"]:
        return f"account is now {account['provider']}, authorized as {target['provider']}"
    if account["name"] != target["name"]:
        return f"account is now named {account['name']!r}, authorized as {target['name']!r}"
    if account["disabled"]:
        return "account is disabled in Postiz"
    return None


def caption(hook_title: str, channel: str, live_title: str | None, platform: str = "twitch") -> str:
    name = channel.split("/")[-1].lstrip("@")
    source = f"youtube.com/{channel}" if platform == "youtube" else f"twitch.tv/{channel}"
    tags = [f"#{platform}", "#clips", f"#{re.sub(r'[^A-Za-z0-9]', '', name).lower()}"]
    if live_title and "gta" in live_title.lower():
        tags.insert(0, "#gta6")
    return f"{hook_title}\n\n🎮 {source}\n{' '.join(tags)}"


def provider_settings(provider: str, title: str) -> dict:
    if provider == "tiktok":
        return {
            "__type": "tiktok", "title": title[:90], "privacy_level": "PUBLIC_TO_EVERYONE",
            "duet": False, "stitch": False, "comment": True, "autoAddMusic": "no",
            "brand_content_toggle": False, "brand_organic_toggle": False, "video_made_with_ai": False,
            "content_posting_method": "DIRECT_POST",
        }
    if provider == "youtube":
        return {"__type": "youtube", "title": title[:100], "type": "public", "selfDeclaredMadeForKids": "no"}
    if provider in ("instagram", "instagram-standalone"):
        return {"__type": provider, "post_type": "post"}
    raise ValueError(f"unsupported provider {provider!r}")


def build_post(target: dict, media: dict, title: str, text: str, when: datetime | None = None) -> dict:
    """One Postiz post for one authorized target: a draft or published now."""
    when = when or datetime.now(timezone.utc)
    return {
        "type": target["mode"],
        "date": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "shortLink": False,
        "tags": [],
        "posts": [{
            "integration": {"id": target["integration_id"]},
            "value": [{"content": text, "image": [{"id": media["id"], "path": media["path"]}]}],
            "settings": provider_settings(target["provider"], title),
        }],
    }
