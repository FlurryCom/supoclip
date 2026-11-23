"""Live clipping: the Twitch channels a user follows (fork feature).

The live-recorder service (live/, compose profile "live") reads these rows,
records broadcasts and sends windows through the normal task pipeline. These
routes only manage channels and report what happened. docs/live-clipping.md.
"""
import asyncio
import base64
import json
import math
import os
import re
import shutil
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
from ... import postiz, reform, twitch_trending
from ...edit_kits import KITS, kit_choices
from ...media_storage import MediaStorage
from ...workers.media_cleanup import _delete_finished_local_upload, _finished_local_upload

router = APIRouter(prefix="/live", tags=["live"])

LIVE_CLIPPING = "live_clipping"
# Following is free (offline channels cost nothing); recordings at once are what the machine pays for.
RECORDING_CEILING = max(1, int(os.getenv("LIVE_MAX_RECORDINGS", "10")))  # the machine's hard ceiling
DEFAULT_RECORDINGS = 10  # an account that never chose a number
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
    for key in ("enabled", "chat_filter", "blur_sensitive", "limit_windows", "safe_posting", "framing_auto"):
        if key in data:
            if not isinstance(data[key], bool):
                raise HTTPException(status_code=422, detail=f"{key} must be true or false")
            settings[key] = data[key]
    for key, low, high in (("min_score", 0, 100), ("chat_weight", 0, 100), ("daily_window_cap", 1, 200), ("safe_gap_minutes", 1, 240)):
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
    if "cam_box" in data or "game_box" in data:
        # game box alone = the streamer has no webcam; both null = back to the automatic detector
        cam, game = _box(data.get("cam_box"), "cam_box"), _box(data.get("game_box"), "game_box")
        if cam is not None and game is None or "cam_box" not in data or "game_box" not in data:
            raise HTTPException(status_code=422, detail="Send cam_box and game_box together; a webcam needs a game box")
        settings["cam_box"], settings["game_box"] = cam, game
    return settings


def _box(value: Any, name: str) -> Dict[str, float] | None:
    """A {x, y, w, h} box in fractions of the frame, inside it, at least 4% a side; None is allowed."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"x", "y", "w", "h"} or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) for v in value.values()
    ):
        raise HTTPException(status_code=422, detail=f"{name} must be {{x, y, w, h}} numbers")
    try:
        x, y, w, h = (float(value[k]) for k in ("x", "y", "w", "h"))
    except OverflowError:
        raise HTTPException(status_code=422, detail=f"{name} has a number out of range") from None
    # 0.039: a 4% box the render rounded to even pixels must still save
    if not all(map(math.isfinite, (x, y, w, h))) or x < 0 or y < 0 or w < 0.039 or h < 0.039 or x + w > 1.0001 or y + h > 1.0001:
        raise HTTPException(status_code=422, detail=f"{name} must fit inside the frame, at least 4% a side")
    return {"x": x, "y": y, "w": w, "h": h}


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
        SELECT c.id, c.platform, c.channel, c.enabled, c.chat_filter, c.min_score, c.chat_weight, c.daily_window_cap, c.limit_windows, c.safe_posting, c.safe_gap_minutes, c.waiting_since,
            c.edit_kit, c.blur_sensitive, c.post_visibility, c.cam_box, c.game_box, c.framing_frame_at, c.framing_changed_at, c.framing_notice, c.framing_reason, c.framing_previous, c.framing_auto,
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
            # waiting: live but not recorded (every slot busy or the disk low), even with a recording left open by a restart
            {**channel, "is_live": channel["recording_id"] is not None and channel["waiting_since"] is None,
             "is_waiting": channel["enabled"] and channel["waiting_since"] is not None,
             "windows": by_channel.get(channel["id"], [])[:10]}
            for channel in channels
        ]
    }


def _settings_like(channel: Any) -> Dict[str, Any]:
    """The settings of an existing channel, to give a new one the same setup."""
    if channel is None:
        return {}
    keys = ("enabled", "chat_filter", "min_score", "chat_weight", "daily_window_cap", "edit_kit",
            "post_visibility", "blur_sensitive", "limit_windows", "safe_posting", "safe_gap_minutes")
    return {key: getattr(channel, key) for key in keys}


@router.get("/trending")
async def trending(request: Request, game: str = "gta6", language: str = "en", db: AsyncSession = Depends(get_db)):
    """The most watched Twitch lives right now, marking the ones this account already follows."""
    user_id = await _user(request, db)
    rows = (await db.execute(text(
        "SELECT channel FROM live_channels WHERE user_id = :user_id AND platform = 'twitch'"), {"user_id": user_id})).all()
    try:
        channels = await twitch_trending.top_streams(game, language, {r.channel.lower() for r in rows})
    except twitch_trending.TwitchNotConfigured:
        return {"configured": False, "channels": []}
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't reach Twitch") from None
    return {"configured": True, "channels": channels}


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
    channel_id = str(uuid.uuid4())
    row = (await db.execute(text("""
        INSERT INTO live_channels (id, user_id, platform, channel, enabled, chat_filter, min_score, chat_weight, daily_window_cap, edit_kit, post_visibility, blur_sensitive, limit_windows, safe_posting, safe_gap_minutes)
        VALUES (:id, :user_id, :platform, :channel, :enabled, :chat_filter, :min_score, :chat_weight, :daily_window_cap, :edit_kit, :post_visibility, :blur_sensitive, :limit_windows, :safe_posting, :safe_gap_minutes)
        ON CONFLICT (user_id, platform, channel) DO NOTHING
        RETURNING id
    """), {
        "id": channel_id, "user_id": user_id, "platform": platform, "channel": name,
        "enabled": settings.get("enabled", True), "chat_filter": settings.get("chat_filter", True),
        "min_score": settings.get("min_score", 60), "chat_weight": settings.get("chat_weight", 60),
        "daily_window_cap": settings.get("daily_window_cap", 48), "edit_kit": settings.get("edit_kit", "none"),
        "post_visibility": settings.get("post_visibility", "default"),
        "blur_sensitive": settings.get("blur_sensitive", True), "limit_windows": settings.get("limit_windows", True),
        "safe_posting": settings.get("safe_posting", False), "safe_gap_minutes": settings.get("safe_gap_minutes", 15),
    })).first()
    if row is None:
        raise HTTPException(status_code=409, detail="You already follow this channel")
    await db.commit()
    return {"id": channel_id, "platform": platform, "channel": name}


def slot_warnings(load5: float, cpus: int, free_gb: float) -> list[str]:
    """What the Live page warns about: the processor busy (5-min load over 75% of cores) or under 50 GB of disk."""
    warnings = []
    if load5 > 0.75 * max(cpus, 1):
        warnings.append("cpu")
    if free_gb < 50:
        warnings.append("disk")
    return warnings


def account_slots(limit: int | None, ceiling: int, owners: int) -> int:
    """Lives the account really records at once: its number, the machine's ceiling, and its fair share (as the recorder)."""
    return min(limit or DEFAULT_RECORDINGS, ceiling, max(1, -(-ceiling // max(owners, 1))))


def _slots_limit(data: Dict[str, Any]) -> int:
    value = data.get("limit")
    if type(value) is not int or not 1 <= value <= RECORDING_CEILING:
        raise HTTPException(status_code=422, detail=f"limit must be a whole number from 1 to {RECORDING_CEILING}")
    return value


@router.get("/slots")
async def recording_slots(request: Request, db: AsyncSession = Depends(get_db)):
    """Lives recording now against the account's number, channels waiting for a slot, and machine warnings."""
    user_id = await _user(request, db)
    row = (await db.execute(text("""
        SELECT
            -- A recording left open by a restart while its channel waits for a slot is not recording.
            (SELECT count(*) FROM live_recordings r JOIN live_channels c ON c.id = r.channel_id
             WHERE c.user_id = :user_id AND r.ended_at IS NULL AND c.waiting_since IS NULL) AS recording,
            (SELECT count(*) FROM live_channels c WHERE c.user_id = :user_id AND c.enabled AND c.waiting_since IS NOT NULL) AS waiting,
            (SELECT max_recordings FROM live_accounts WHERE user_id = :user_id) AS max_recordings,
            (SELECT count(DISTINCT user_id) FROM live_channels WHERE enabled) AS owners
    """), {"user_id": user_id})).first()
    load5, cpus = os.getloadavg()[1], os.cpu_count() or 1
    free_gb = shutil.disk_usage("/app/uploads").free / 2**30  # clips and live recordings share this disk
    return {
        "recording": row.recording, "waiting": row.waiting,
        "limit": min(row.max_recordings or DEFAULT_RECORDINGS, RECORDING_CEILING), "ceiling": RECORDING_CEILING,
        "share": account_slots(row.max_recordings, RECORDING_CEILING, row.owners),
        "warnings": slot_warnings(load5, cpus, free_gb),
        "cpu_percent": round(100 * load5 / cpus), "disk_free_gb": int(free_gb),
    }


@router.patch("/slots")
async def update_recording_slots(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    limit = _slots_limit(await _body(request))
    await db.execute(text("""
        INSERT INTO live_accounts (user_id, max_recordings) VALUES (:user_id, :limit)
        ON CONFLICT (user_id) DO UPDATE SET max_recordings = EXCLUDED.max_recordings, updated_at = now()
    """), {"user_id": user_id, "limit": limit})
    await db.commit()
    return {"limit": limit}


@router.patch("/channels/{channel_id}")
async def update_channel(channel_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    settings = _settings(await _body(request))
    if not settings:
        raise HTTPException(status_code=422, detail="Nothing to update")
    boxes = {"cam_box", "game_box"}
    # keys come from _settings' allowlist
    assignments = ", ".join(f"{key} = CAST(:{key} AS jsonb)" if key in boxes else f"{key} = :{key}" for key in settings)
    values = {key: json.dumps(value) if key in boxes and value is not None else value for key, value in settings.items()}
    result = await db.execute(
        text(f"UPDATE live_channels SET {assignments}, updated_at = now() WHERE id = :id AND user_id = :user_id"),
        {**values, "id": channel_id, "user_id": user_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Channel not found")
    if boxes & settings.keys():
        # set by hand (or "Undo" / "It's right"): the notice goes
        await db.execute(text(
            "UPDATE live_channels SET framing_changed_at = NULL, framing_notice = NULL, framing_reason = NULL, "
            "framing_previous = NULL WHERE id = :id"
        ), {"id": channel_id})
    await db.commit()
    return {"id": channel_id, **settings}


@router.get("/channels/{channel_id}/framing-frame")
async def framing_frame(channel_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """A recent frame of the channel's live, to mark the webcam and the game on (JSON: the proxy only passes text)."""
    user_id = await _user(request, db)
    row = (await db.execute(
        text("SELECT framing_frame, framing_frame_at FROM live_channels WHERE id = :id AND user_id = :user_id"),
        {"id": channel_id, "user_id": user_id},
    )).first()
    if row is None or row.framing_frame is None:
        raise HTTPException(status_code=404, detail="No frame yet")
    image = base64.b64encode(bytes(row.framing_frame)).decode()
    return {"image": f"data:image/jpeg;base64,{image}", "at": row.framing_frame_at.isoformat()}


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
        SELECT t.id, t.integration_id, t.provider, t.name, t.enabled, t.min_score, t.mode, t.visibility, t.daily_cap, t.blur_sensitive, t.safe_posting, t.limit_posts, t.safe_gap_minutes, t.process_min_score,
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
    for key, low, high in (("min_score", 0, 100), ("daily_cap", 1, 100), ("safe_gap_minutes", 1, 240), ("process_min_score", 0, 100)):
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


def publish_plan(clip_ids: list[str], target_ids: list[str], already: set[tuple[str, str]]) -> list[tuple[str, str]]:
    """(clip, account) pairs to queue: each live clip once per chosen account, minus pairs already posted or queued."""
    return [(c, t) for c in clip_ids for t in target_ids if (c, t) not in already]


def _id_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 200 or not all(isinstance(v, str) and 0 < len(v) <= 36 for v in value):
        raise HTTPException(status_code=422, detail=f"{name} must be a list of 1-200 ids")
    return list(dict.fromkeys(value))


@router.post("/publish")
async def publish_clips(request: Request, db: AsyncSession = Depends(get_db)):
    """Queue every live clip of the chosen generations for the chosen accounts, whatever its score.

    The recorder posts them like automatic posts (CTA, blur, safe posting gap), never twice on one account.
    """
    user_id = await _postiz_user(request, db)
    body = await _body(request)
    task_ids = _id_list(body.get("task_ids"), "task_ids")
    target_ids = _id_list(body.get("target_ids"), "target_ids")
    targets = [r[0] for r in (await db.execute(text(
        "SELECT id FROM live_post_targets WHERE user_id = :u AND enabled AND id = ANY(:ids) ORDER BY created_at"
    ), {"u": user_id, "ids": target_ids})).all()]
    if not targets:
        raise HTTPException(status_code=422, detail="No active account chosen")
    rows = (await db.execute(text("""
        SELECT g.id, (lc.clip_id IS NOT NULL OR EXISTS (SELECT 1 FROM reform_items ri WHERE ri.task_id = g.task_id)) AS live
        FROM generated_clips g
        JOIN tasks tk ON tk.id = g.task_id AND tk.user_id = :u
        LEFT JOIN live_clips lc ON lc.clip_id = g.id
        WHERE g.task_id = ANY(:tasks) ORDER BY g.task_id, g.clip_order
    """), {"u": user_id, "tasks": task_ids})).all()
    clips = [r[0] for r in rows if r[1]]
    already = {tuple(r) for r in (await db.execute(text("""
        SELECT clip_id, target_id FROM live_posts WHERE clip_id = ANY(:c) AND target_id = ANY(:t)
        UNION SELECT clip_id, target_id FROM live_post_requests WHERE clip_id = ANY(:c) AND target_id = ANY(:t)
    """), {"c": clips, "t": targets})).all()} if clips else set()
    plan = publish_plan(clips, targets, already)
    for clip_id, target_id in plan:
        await db.execute(text(
            "INSERT INTO live_post_requests (id, user_id, clip_id, target_id) VALUES (:id, :u, :c, :t) "
            "ON CONFLICT (clip_id, target_id) DO NOTHING"
        ), {"id": str(uuid.uuid4()), "u": user_id, "c": clip_id, "t": target_id})
    await db.commit()
    return {"queued": len(plan), "already": len(clips) * len(targets) - len(plan), "not_live": len(rows) - len(clips)}


_durations: dict[str, float] = {}  # yt-dlp lookups are slow; a video's length doesn't change
_DURATION_GATE = asyncio.Semaphore(8)  # shared by all requests: at most 8 yt-dlp lookups at once


def _flat_list(listing_url: str, page: int) -> dict[str, Any]:
    import yt_dlp
    options = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": "in_playlist",
               "playliststart": (page - 1) * 30 + 1, "playlistend": page * 30}
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(listing_url, download=False) or {}


def _video_duration(url: str) -> float | None:
    import yt_dlp
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            duration = (ydl.extract_info(url, download=False) or {}).get("duration")
            return float(duration) if duration else None
    except Exception:
        return None


@router.get("/reform/list")
async def reform_list(request: Request, url: str = "", page: int = 1, db: AsyncSession = Depends(get_db)):
    """A channel's recent videos for the Reformular page, 30 per page."""
    user_id = await _user(request, db)
    try:
        platform, listing_url, channel = reform.normalize_source(url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    page = max(1, min(page, 20))
    try:
        items = reform.listing_items(await run_in_threadpool(_flat_list, listing_url, page), platform)
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't read this channel") from None
    async def fill(item: dict[str, Any]) -> None:
        key = f"{platform}:{item['id']}"
        if key not in _durations:
            async with _DURATION_GATE:
                duration = await run_in_threadpool(_video_duration, reform.video_url(platform, item["id"], channel))
            if duration is None:
                return  # failed lookups aren't cached, so a later listing retries
            if len(_durations) > 20000:
                _durations.clear()  # shortcut: crude bound; swap for an LRU if listings get heavy
            _durations[key] = duration
        item["duration"] = _durations[key]

    await asyncio.gather(*(fill(i) for i in items if i["duration"] is None))
    ids = [i["id"] for i in items]
    taken = {r[0] for r in (await db.execute(text("""
        SELECT video_id FROM reform_items WHERE user_id = :u AND platform = :p AND video_id = ANY(:ids)
          AND (status IN ('pending', 'submitted') OR (status = 'done' AND task_id IS NOT NULL))
    """), {"u": user_id, "p": platform, "ids": ids})).all()} if ids else set()
    for item in items:
        duration = item["duration"]
        item["short"] = duration is not None and duration < reform.SHORT_SECONDS
        item["too_long"] = duration is not None and duration > reform.MAX_SECONDS
        item["taken"] = item["id"] in taken
    return {"platform": platform, "channel": channel, "items": items}


def reform_request(body: Dict[str, Any]) -> list[Dict[str, Any]]:
    """The valid videos of a Reformular request, with server-built URLs (client URLs are ignored)."""
    raw = body.get("items")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 30:
        raise HTTPException(status_code=422, detail="Choose 1 to 30 videos")
    items = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        platform, video_id, channel = item.get("platform"), item.get("id"), item.get("channel")
        duration = item.get("duration")
        if not (reform.valid_video_id(platform, video_id) and reform.valid_channel(platform, channel)):
            continue
        if isinstance(duration, (int, float)) and duration > reform.MAX_SECONDS:
            continue
        items.append({
            "platform": platform, "video_id": video_id, "source_channel": channel,
            "source_url": reform.video_url(platform, video_id, channel),
            "title": str(item.get("title") or "")[:300],
            "duration": float(duration) if isinstance(duration, (int, float)) else None,
        })
    return items


@router.post("/reform")
async def reform_queue(request: Request, db: AsyncSession = Depends(get_db)):
    """Queue videos to remake; the recorder submits them one at a time so lives aren't held up."""
    user_id = await _postiz_user(request, db)
    body = await _body(request)
    kit = body.get("edit_kit") or "catclips_v2"
    if kit not in KITS:
        raise HTTPException(status_code=422, detail="Unknown edit kit")
    items = reform_request(body)
    raw_targets = body.get("target_ids") or []
    target_ids = _id_list(raw_targets, "target_ids") if raw_targets else []
    targets = [r[0] for r in (await db.execute(text(
        "SELECT id FROM live_post_targets WHERE user_id = :u AND enabled AND id = ANY(:ids)"
    ), {"u": user_id, "ids": target_ids})).all()] if target_ids else []
    auto_publish = body.get("auto_publish") is True
    if auto_publish and not targets:
        raise HTTPException(status_code=422, detail="No active account chosen")
    queued = 0
    for item in items:
        row = (await db.execute(text("""
            INSERT INTO reform_items (id, user_id, platform, video_id, source_url, source_channel, title, duration,
                edit_kit, auto_publish, target_ids)
            VALUES (:id, :u, :platform, :video_id, :source_url, :source_channel, :title, :duration, :kit, :auto, :targets)
            ON CONFLICT (user_id, platform, video_id) DO UPDATE SET status = 'pending', task_id = NULL, reason = NULL,
                source_url = EXCLUDED.source_url, source_channel = EXCLUDED.source_channel, title = EXCLUDED.title,
                duration = EXCLUDED.duration, edit_kit = EXCLUDED.edit_kit, auto_publish = EXCLUDED.auto_publish,
                target_ids = EXCLUDED.target_ids, updated_at = now()
            WHERE reform_items.status = 'error' OR (reform_items.status = 'done' AND reform_items.task_id IS NULL)
            RETURNING id
        """), {**item, "id": str(uuid.uuid4()), "u": user_id, "kit": kit, "auto": auto_publish,
               "targets": targets if auto_publish else []})).first()
        queued += row is not None
    await db.commit()
    return {"queued": queued, "skipped": len(body["items"]) - queued}


@router.get("/reform/items")
async def reform_items_list(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    rows = (await db.execute(text("""
        SELECT id, title, platform, status, reason, task_id, created_at FROM reform_items
        WHERE user_id = :u ORDER BY updated_at DESC LIMIT 20
    """), {"u": user_id})).mappings().all()
    return {"items": [dict(r) for r in rows]}


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
