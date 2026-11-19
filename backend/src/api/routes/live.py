"""Live clipping: the Twitch channels a user follows (fork feature).

The live-recorder service (live/, compose profile "live") reads these rows,
records broadcasts and sends windows through the normal task pipeline. These
routes only manage channels and report what happened. docs/live-clipping.md.
"""
import re
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import get_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...feature_flags import client_platform, is_enabled

router = APIRouter(prefix="/live", tags=["live"])

LIVE_CLIPPING = "live_clipping"
CHANNEL_RE = re.compile(r"^[a-z0-9_]{3,25}$")
TWITCH_URL_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]{3,25})/?$")


async def _user(request: Request, db: AsyncSession) -> str:
    user_id = get_authenticated_user_id(request, get_config())
    if not await is_enabled(db, LIVE_CLIPPING, platform=client_platform(request), user_id=user_id):
        raise HTTPException(status_code=404, detail="Not found")
    return user_id


def _channel_name(raw: Any) -> str:
    """A Twitch login from ``name``, ``@name`` or a twitch.tv link."""
    value = str(raw or "").strip()
    match = TWITCH_URL_RE.match(value)
    name = (match.group(1) if match else value.lstrip("@")).lower()
    if not CHANNEL_RE.fullmatch(name):
        raise HTTPException(status_code=422, detail="Enter a Twitch channel name or twitch.tv link")
    return name


def _settings(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validated optional settings from a request body."""
    settings: Dict[str, Any] = {}
    for key in ("enabled", "chat_filter"):
        if key in data:
            if not isinstance(data[key], bool):
                raise HTTPException(status_code=422, detail=f"{key} must be true or false")
            settings[key] = data[key]
    for key, low, high in (("min_score", 0, 100), ("daily_window_cap", 1, 200)):
        if key in data:
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise HTTPException(status_code=422, detail=f"{key} must be a whole number from {low} to {high}")
            settings[key] = value
    return settings


async def _body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")
    return data


@router.get("/channels")
async def list_channels(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    channels = (await db.execute(text("""
        SELECT c.id, c.platform, c.channel, c.enabled, c.chat_filter, c.min_score, c.daily_window_cap,
            r.id AS recording_id, r.title AS live_title, r.started_at AS live_since
        FROM live_channels c
        LEFT JOIN LATERAL (
            SELECT id, title, started_at FROM live_recordings
            WHERE channel_id = c.id AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1
        ) r ON true
        WHERE c.user_id = :user_id ORDER BY c.created_at
    """), {"user_id": user_id})).mappings().all()
    windows = (await db.execute(text("""
        SELECT r.channel_id, w.id, w.window_start, w.window_end, w.status, w.reason, w.task_id
        FROM live_windows w
        JOIN live_recordings r ON r.id = w.recording_id
        JOIN live_channels c ON c.id = r.channel_id
        WHERE c.user_id = :user_id AND w.created_at > now() - interval '3 days'
        ORDER BY w.created_at DESC LIMIT 100
    """), {"user_id": user_id})).mappings().all()
    by_channel: Dict[str, list] = {}
    for window in windows:
        by_channel.setdefault(window["channel_id"], []).append({k: v for k, v in window.items() if k != "channel_id"})
    return {
        "channels": [
            {**channel, "is_live": channel["recording_id"] is not None, "windows": by_channel.get(channel["id"], [])[:10]}
            for channel in channels
        ]
    }


@router.post("/channels")
async def add_channel(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    data = await _body(request)
    name = _channel_name(data.get("channel"))
    settings = _settings(data)
    channel_id = str(uuid.uuid4())
    row = (await db.execute(text("""
        INSERT INTO live_channels (id, user_id, platform, channel, enabled, chat_filter, min_score, daily_window_cap)
        VALUES (:id, :user_id, 'twitch', :channel, :enabled, :chat_filter, :min_score, :daily_window_cap)
        ON CONFLICT (user_id, platform, channel) DO NOTHING
        RETURNING id
    """), {
        "id": channel_id, "user_id": user_id, "channel": name,
        "enabled": settings.get("enabled", True), "chat_filter": settings.get("chat_filter", True),
        "min_score": settings.get("min_score", 60), "daily_window_cap": settings.get("daily_window_cap", 12),
    })).first()
    if row is None:
        raise HTTPException(status_code=409, detail="You already follow this channel")
    await db.commit()
    return {"id": channel_id, "channel": name}


@router.patch("/channels/{channel_id}")
async def update_channel(channel_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    settings = _settings(await _body(request))
    if not settings:
        raise HTTPException(status_code=422, detail="Nothing to update")
    assignments = ", ".join(f"{key} = :{key}" for key in settings)  # keys come from _settings' allowlist
    result = await db.execute(
        text(f"UPDATE live_channels SET {assignments}, updated_at = now() WHERE id = :id AND user_id = :user_id"),
        {**settings, "id": channel_id, "user_id": user_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Channel not found")
    await db.commit()
    return {"id": channel_id, **settings}


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Stop following. Clips already made stay in the library."""
    user_id = await _user(request, db)
    result = await db.execute(
        text("DELETE FROM live_channels WHERE id = :id AND user_id = :user_id"),
        {"id": channel_id, "user_id": user_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Channel not found")
    await db.commit()
    return {"deleted": channel_id}
