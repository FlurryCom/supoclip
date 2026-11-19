"""Live clipping: the Twitch channels a user follows (fork feature).

The live-recorder service (live/, compose profile "live") reads these rows,
records broadcasts and sends windows through the normal task pipeline. These
routes only manage channels and report what happened. docs/live-clipping.md.
"""
import os
import re
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import get_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...feature_flags import client_platform, is_enabled
from ... import postiz, twitch_trending
from ...edit_kits import KITS, kit_choices
from ...media_storage import MediaStorage
from ...workers.media_cleanup import _delete_finished_local_upload, _finished_local_upload

router = APIRouter(prefix="/live", tags=["live"])

LIVE_CLIPPING = "live_clipping"
MAX_CHANNELS_PER_USER = max(1, int(os.getenv("LIVE_MAX_CHANNELS_PER_USER", "5")))
CHANNEL_RE = re.compile(r"^[a-z0-9_]{3,25}$")
TWITCH_URL_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]{3,25})/?$")
YOUTUBE_HANDLE_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.)?youtube\.com/(@[A-Za-z0-9._-]{3,30})(?:/(?:live|streams|videos|featured))?/?(?:\?.*)?$")
YOUTUBE_CHANNEL_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.)?youtube\.com/channel/(UC[A-Za-z0-9_-]{22})(?:/.*)?$")
YOUTUBE_VIDEO_RE = re.compile(
    r"^(?:https?://)?(?:(?:www\.|m\.)?youtube\.com/(?:watch\?(?:.*&)?v=|live/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


async def _user(request: Request, db: AsyncSession) -> str:
    user_id = get_authenticated_user_id(request, get_config())
    if not await is_enabled(db, LIVE_CLIPPING, platform=client_platform(request), user_id=user_id):
        raise HTTPException(status_code=404, detail="Not found")
    return user_id


CHANNEL_HELP = "Enter a Twitch channel name or link, or a YouTube channel (youtube.com/@name) or live link"


def _channel_name(raw: Any) -> str:
    """A Twitch login from ``name``, ``@name`` or a twitch.tv link."""
    value = str(raw or "").strip()
    match = TWITCH_URL_RE.match(value)
    name = (match.group(1) if match else value.lstrip("@")).lower()
    if not CHANNEL_RE.fullmatch(name):
        raise HTTPException(status_code=422, detail=CHANNEL_HELP)
    return name


def _youtube_channel_of_video(video_id: str) -> str:
    """The channel a YouTube video or live belongs to: ``@handle`` or ``channel/UC...``."""
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False, process=False)
    except Exception:
        raise HTTPException(status_code=422, detail="Couldn't find the YouTube channel of that link") from None
    handle = str(info.get("uploader_id") or "")
    if re.fullmatch(r"@[A-Za-z0-9._-]{3,30}", handle):
        return handle.lower()
    channel_id = str(info.get("channel_id") or "")
    if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", channel_id):
        return f"channel/{channel_id}"
    raise HTTPException(status_code=422, detail="Couldn't find the YouTube channel of that link")


async def _channel(raw: Any) -> tuple[str, str]:
    """(platform, channel) from a Twitch name/link or a YouTube channel/video link.

    A YouTube video or live link is resolved to its channel, so every later live
    of that channel is followed, not just this one.
    """
    value = str(raw or "").strip()
    if match := YOUTUBE_HANDLE_RE.match(value):
        return "youtube", match.group(1).lower()
    if match := YOUTUBE_CHANNEL_RE.match(value):
        return "youtube", f"channel/{match.group(1)}"
    if match := YOUTUBE_VIDEO_RE.match(value):
        return "youtube", await run_in_threadpool(_youtube_channel_of_video, match.group(1))
    return "twitch", _channel_name(value)


def _settings(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validated optional settings from a request body."""
    settings: Dict[str, Any] = {}
    for key in ("enabled", "chat_filter", "blur_sensitive", "limit_windows"):
        if key in data:
            if not isinstance(data[key], bool):
                raise HTTPException(status_code=422, detail=f"{key} must be true or false")
            settings[key] = data[key]
    for key, low, high in (("min_score", 0, 100), ("chat_weight", 0, 100), ("daily_window_cap", 1, 200)):
        if key in data:
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise HTTPException(status_code=422, detail=f"{key} must be a whole number from {low} to {high}")
            settings[key] = value
    if "post_visibility" in data:
        if data["post_visibility"] not in ("default", "unlisted", "private"):
            raise HTTPException(status_code=422, detail="post_visibility must be default, unlisted or private")
        settings["post_visibility"] = data["post_visibility"]
    if "edit_kit" in data:
        if not isinstance(data["edit_kit"], str) or data["edit_kit"] not in KITS:
            raise HTTPException(status_code=422, detail=f"edit_kit must be one of: {', '.join(KITS)}")
        settings["edit_kit"] = data["edit_kit"]
    return settings


async def _body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")
    return data


@router.get("/edit-kits")
async def list_edit_kits(request: Request, db: AsyncSession = Depends(get_db)):
    await _user(request, db)
    return kit_choices()


@router.get("/channels")
async def list_channels(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    channels = (await db.execute(text("""
        SELECT c.id, c.platform, c.channel, c.enabled, c.chat_filter, c.min_score, c.chat_weight, c.daily_window_cap, c.limit_windows,
            c.edit_kit, c.blur_sensitive, c.post_visibility, r.id AS recording_id, r.title AS live_title, r.started_at AS live_since
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
    clips = (await db.execute(text("""
        SELECT lc.window_id, g.id, g.task_id, g.hook_title,
            -- Clips kept before chat heat existed only have the AI's score.
            COALESCE(lc.ai_score, g.virality_score) AS ai_score, lc.chat_heat,
            COALESCE(lc.final_score, g.virality_score) AS final_score,
            COALESCE((
                SELECT json_agg(json_build_object('provider', t.provider, 'name', t.name, 'status', p.status, 'detail', p.detail))
                FROM live_posts p JOIN live_post_targets t ON t.id = p.target_id WHERE p.clip_id = g.id
            ), '[]'::json) AS posts
        FROM live_clips lc
        JOIN generated_clips g ON g.id = lc.clip_id
        JOIN live_windows w ON w.id = lc.window_id
        JOIN live_recordings r ON r.id = w.recording_id
        JOIN live_channels c ON c.id = r.channel_id
        WHERE c.user_id = :user_id AND w.created_at > now() - interval '3 days'
        ORDER BY lc.final_score DESC NULLS LAST
    """), {"user_id": user_id})).mappings().all()
    by_window: Dict[str, list] = {}
    for clip in clips:
        by_window.setdefault(clip["window_id"], []).append({k: v for k, v in clip.items() if k != "window_id"})
    by_channel: Dict[str, list] = {}
    for window in windows:
        entry = {k: v for k, v in window.items() if k != "channel_id"}
        entry["clips"] = by_window.get(window["id"], [])
        by_channel.setdefault(window["channel_id"], []).append(entry)
    return {
        "channels": [
            {**channel, "is_live": channel["recording_id"] is not None, "windows": by_channel.get(channel["id"], [])[:10]}
            for channel in channels
        ]
    }


def _settings_like(channel: Any) -> Dict[str, Any]:
    """The settings of an existing channel, to give a new one the same setup."""
    if channel is None:
        return {}
    keys = ("enabled", "chat_filter", "min_score", "chat_weight", "daily_window_cap", "edit_kit",
            "post_visibility", "blur_sensitive", "limit_windows")
    return {key: getattr(channel, key) for key in keys}


@router.get("/trending")
async def trending(request: Request, game: str = "gta6", language: str = "en", db: AsyncSession = Depends(get_db)):
    """The most watched Twitch lives right now, marking the ones this account already follows."""
    user_id = await _user(request, db)
    rows = (await db.execute(text(
        "SELECT channel FROM live_channels WHERE user_id = :user_id AND platform = 'twitch'"), {"user_id": user_id})).all()
    count = (await db.execute(text("SELECT count(*) FROM live_channels WHERE user_id = :user_id"), {"user_id": user_id})).scalar()
    try:
        channels = await twitch_trending.top_streams(game, language, {r.channel.lower() for r in rows})
    except twitch_trending.TwitchNotConfigured:
        return {"configured": False, "channels": [], "at_limit": count >= MAX_CHANNELS_PER_USER}
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't reach Twitch") from None
    return {"configured": True, "channels": channels, "at_limit": count >= MAX_CHANNELS_PER_USER,
            "limit": MAX_CHANNELS_PER_USER}


@router.post("/channels")
async def add_channel(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    data = await _body(request)
    platform, name = await _channel(data.get("channel"))
    settings = _settings(data)
    if data.get("like_last") is True:  # "Adicionar" on trending: same settings as the last channel added
        last = (await db.execute(text(
            "SELECT * FROM live_channels WHERE user_id = :user_id ORDER BY created_at DESC LIMIT 1"
        ), {"user_id": user_id})).first()
        settings = {**_settings_like(last), **settings}
    # Each live channel is a recording process plus disk, so cap how many one account follows.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:user_id, 2911))"), {"user_id": user_id})
    count = (await db.execute(text("SELECT count(*) FROM live_channels WHERE user_id = :user_id"), {"user_id": user_id})).scalar()
    if count >= MAX_CHANNELS_PER_USER:
        raise HTTPException(status_code=409, detail=f"You can follow up to {MAX_CHANNELS_PER_USER} channels")
    channel_id = str(uuid.uuid4())
    row = (await db.execute(text("""
        INSERT INTO live_channels (id, user_id, platform, channel, enabled, chat_filter, min_score, chat_weight, daily_window_cap, edit_kit, post_visibility, blur_sensitive, limit_windows)
        VALUES (:id, :user_id, :platform, :channel, :enabled, :chat_filter, :min_score, :chat_weight, :daily_window_cap, :edit_kit, :post_visibility, :blur_sensitive, :limit_windows)
        ON CONFLICT (user_id, platform, channel) DO NOTHING
        RETURNING id
    """), {
        "id": channel_id, "user_id": user_id, "platform": platform, "channel": name,
        "enabled": settings.get("enabled", True), "chat_filter": settings.get("chat_filter", True),
        "min_score": settings.get("min_score", 60), "chat_weight": settings.get("chat_weight", 60),
        "daily_window_cap": settings.get("daily_window_cap", 48), "edit_kit": settings.get("edit_kit", "none"),
        "post_visibility": settings.get("post_visibility", "default"),
        "blur_sensitive": settings.get("blur_sensitive", True), "limit_windows": settings.get("limit_windows", True),
    })).first()
    if row is None:
        raise HTTPException(status_code=409, detail="You already follow this channel")
    await db.commit()
    return {"id": channel_id, "platform": platform, "channel": name}


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


async def _postiz_user(request: Request, db: AsyncSession) -> str:
    """The server has one Postiz key, so only allowlisted SupoClip accounts may use it."""
    user_id = await _user(request, db)
    if user_id not in postiz.allowed_user_ids():
        raise HTTPException(status_code=403, detail="This account can't use this server's Postiz")
    return user_id


async def _postiz_accounts() -> list[Dict[str, Any]]:
    try:
        return await postiz.list_integrations()
    except postiz.PostizNotConfigured:
        raise HTTPException(status_code=409, detail="Postiz is not configured on this server") from None
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't reach Postiz") from None


@router.get("/postiz/accounts")
async def list_postiz_accounts(request: Request, db: AsyncSession = Depends(get_db)):
    """Postiz accounts beside this user's authorizations, flagging any that changed."""
    user_id = await _user(request, db)
    if not postiz.configured():
        return {"configured": False, "allowed": False, "accounts": [], "targets": []}
    try:
        user_id = await _postiz_user(request, db)
    except HTTPException:
        return {"configured": True, "allowed": False, "accounts": [], "targets": []}
    accounts = await _postiz_accounts()
    targets = (await db.execute(text("""
        SELECT t.id, t.integration_id, t.provider, t.name, t.enabled, t.min_score, t.mode, t.visibility, t.daily_cap, t.blur_sensitive, t.safe_posting, t.limit_posts,
            (SELECT count(*) FROM live_posts p WHERE p.target_id = t.id AND p.status = 'posted'
                AND p.created_at > now() - interval '1 day') AS posted_today
        FROM live_post_targets t WHERE t.user_id = :user_id ORDER BY t.created_at
    """), {"user_id": user_id})).mappings().all()
    by_id = {a["id"]: a for a in accounts}
    result = []
    for target in targets:
        account = by_id.get(target["integration_id"])
        problem = None
        if account is None:
            problem = "missing"
        elif account["provider"] != target["provider"] or account["name"] != target["name"]:
            problem = "changed"
        elif account["disabled"]:
            problem = "disabled"
        result.append({**target, "problem": problem})
    authorized = {t["integration_id"] for t in targets}
    return {
        "configured": True,
        "allowed": True,
        "accounts": [
            {**a, "supported": a["provider"] in postiz.SUPPORTED_PROVIDERS, "authorized": a["id"] in authorized}
            for a in accounts
        ],
        "targets": result,
    }


@router.post("/postiz/targets")
async def authorize_postiz_account(request: Request, db: AsyncSession = Depends(get_db)):
    """Authorize one Postiz account to receive live clips. Provider and name come from Postiz."""
    user_id = await _postiz_user(request, db)
    data = await _body(request)
    integration_id = str(data.get("integration_id") or "").strip()
    account = next((a for a in await _postiz_accounts() if a["id"] == integration_id), None)
    if account is None:
        raise HTTPException(status_code=404, detail="That account isn't connected in Postiz")
    if account["provider"] not in postiz.SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=422, detail="Only TikTok, YouTube and Instagram accounts can receive clips")
    settings = _target_settings(data)
    target_id = str(uuid.uuid4())
    row = (await db.execute(text("""
        INSERT INTO live_post_targets (id, user_id, integration_id, provider, name, min_score, mode, visibility, daily_cap)
        VALUES (:id, :user_id, :integration_id, :provider, :name, :min_score, :mode, :visibility, :daily_cap)
        ON CONFLICT (user_id, integration_id) DO NOTHING RETURNING id
    """), {
        "id": target_id, "user_id": user_id, "integration_id": integration_id,
        "provider": account["provider"], "name": account["name"],
        "min_score": settings.get("min_score", 80), "mode": settings.get("mode", "draft"),
        # TikTok accounts start private: unaudited apps can't post publicly.
        "visibility": settings.get("visibility", "private" if account["provider"] == "tiktok" else "public"),
        "daily_cap": settings.get("daily_cap", 100),  # owner: up to 100 posts a day per account
    })).first()
    if row is None:
        raise HTTPException(status_code=409, detail="This account is already authorized")
    await db.commit()
    return {"id": target_id, "provider": account["provider"], "name": account["name"]}


def _target_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    settings: Dict[str, Any] = {}
    for key in ("enabled", "blur_sensitive", "safe_posting", "limit_posts"):
        if key in data:
            if not isinstance(data[key], bool):
                raise HTTPException(status_code=422, detail=f"{key} must be true or false")
            settings[key] = data[key]
    if "mode" in data:
        if data["mode"] not in ("draft", "now"):
            raise HTTPException(status_code=422, detail="mode must be draft or now")
        settings["mode"] = data["mode"]
    if "visibility" in data:
        if data["visibility"] not in ("public", "private"):
            raise HTTPException(status_code=422, detail="visibility must be public or private")
        settings["visibility"] = data["visibility"]
    for key, low, high in (("min_score", 0, 100), ("daily_cap", 1, 100)):
        if key in data:
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise HTTPException(status_code=422, detail=f"{key} must be a whole number from {low} to {high}")
            settings[key] = value
    return settings


@router.patch("/postiz/targets/{target_id}")
async def update_postiz_target(target_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _postiz_user(request, db)
    settings = _target_settings(await _body(request))
    if not settings:
        raise HTTPException(status_code=422, detail="Nothing to update")
    assignments = ", ".join(f"{key} = :{key}" for key in settings)  # keys come from _target_settings' allowlist
    result = await db.execute(
        text(f"UPDATE live_post_targets SET {assignments} WHERE id = :id AND user_id = :user_id"),
        {**settings, "id": target_id, "user_id": user_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Account not found")
    await db.commit()
    return {"id": target_id, **settings}


@router.delete("/postiz/targets/{target_id}")
async def revoke_postiz_target(target_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Stop posting to this account. Posts already made stay on the platform."""
    user_id = await _postiz_user(request, db)
    result = await db.execute(
        text("DELETE FROM live_post_targets WHERE id = :id AND user_id = :user_id"),
        {"id": target_id, "user_id": user_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Account not found")
    await db.commit()
    return {"revoked": target_id}


@router.post("/windows/{window_id}/release-source")
async def release_window_source(window_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Delete a finished window's uploaded copy of the broadcast. Its clips stay.

    Called by the live recorder once a window's clips are rendered and scored;
    each copy is 1-2 GB and would otherwise stay on disk for good.
    """
    user_id = await _user(request, db)
    row = (await db.execute(text("""
        SELECT w.status, w.source_released, s.url
        FROM live_windows w
        JOIN live_recordings r ON r.id = w.recording_id
        JOIN live_channels c ON c.id = r.channel_id
        LEFT JOIN tasks t ON t.id = w.task_id
        LEFT JOIN sources s ON s.id = t.source_id
        WHERE w.id = :id AND c.user_id = :user_id
    """), {"id": window_id, "user_id": user_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Window not found")
    if row.status not in ("done", "error"):
        raise HTTPException(status_code=409, detail="Window is still processing")
    if not row.source_released and row.url and row.url.startswith("upload://") and not row.url.startswith("upload://s3/"):
        storage = MediaStorage()
        path = _finished_local_upload(storage, row.url)
        _delete_finished_local_upload(storage, row.url)
        if path is not None:
            storage.delete(str(path.with_suffix(".transcription.mp3")))
    await db.execute(text("UPDATE live_windows SET source_released = true WHERE id = :id"), {"id": window_id})
    await db.commit()
    return {"released": window_id}


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
