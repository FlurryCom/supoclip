"""Remaking ready-made clips from other channels with an edit kit (fork feature, "Reformular").

The backend lists a channel's videos and records requests; the live recorder downloads
and submits them; ``reform_segments`` makes the worker render the whole video.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np
from sqlalchemy import text

from .database import get_session_maker
from .media.timeline import seconds_to_mmss

MAX_SECONDS = 180  # longer sources aren't ready-made cuts
SHORT_SECONDS = 61  # TikTok only monetizes videos longer than a minute (see clip_duration.py)
_IDS = {
    "youtube": re.compile(r"[A-Za-z0-9_-]{11}"),
    "tiktok": re.compile(r"\d{8,25}"),
    "twitch": re.compile(r"[A-Za-z0-9_-]{1,100}"),
}
_YT_HANDLE = re.compile(r"@[A-Za-z0-9._-]{1,100}")
_TIKTOK_USER = re.compile(r"@([A-Za-z0-9._]{1,100})")
_TWITCH_LOGIN = re.compile(r"[A-Za-z0-9_]{1,25}")


def normalize_source(url: str) -> tuple[str, str, str]:
    """(platform, listing URL, channel) for a channel link; ValueError for anything else."""
    raw = (url or "").strip()
    if raw and "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("unsupported")
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    first = next((p for p in parsed.path.split("/") if p), "")
    if host == "instagram.com":
        raise ValueError("instagram")
    if host == "youtube.com" and _YT_HANDLE.fullmatch(first):
        return "youtube", f"https://www.youtube.com/{first}/shorts", first
    if host == "tiktok.com" and (m := _TIKTOK_USER.fullmatch(first)):
        return "tiktok", f"https://www.tiktok.com/@{m.group(1)}", m.group(1)
    if host == "twitch.tv" and _TWITCH_LOGIN.fullmatch(first) and first.lower() not in {"videos", "directory", "search", "settings"}:
        login = first.lower()
        return "twitch", f"https://www.twitch.tv/{login}/clips?filter=clips&range=30d", login
    raise ValueError("unsupported")


_CHANNELS = {"youtube": _YT_HANDLE, "tiktok": re.compile(r"[A-Za-z0-9._]{1,100}"), "twitch": _TWITCH_LOGIN}


def valid_channel(platform: str, channel: str) -> bool:
    pattern = _CHANNELS.get(platform)
    return bool(pattern and isinstance(channel, str) and pattern.fullmatch(channel))


def valid_video_id(platform: str, video_id: str) -> bool:
    pattern = _IDS.get(platform)
    return bool(pattern and isinstance(video_id, str) and pattern.fullmatch(video_id))


def video_url(platform: str, video_id: str, channel: str) -> str:
    if platform == "youtube":
        return f"https://www.youtube.com/shorts/{video_id}"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{channel}/video/{video_id}"
    return f"https://clips.twitch.tv/{video_id}"


def listing_items(info: dict[str, Any], platform: str) -> list[dict[str, Any]]:
    """yt-dlp flat playlist entries as {id, title, duration, thumbnail}."""
    items = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        video_id = str(entry.get("id") or "")
        if platform == "twitch":  # the numeric id doesn't open the clip; the slug does
            video_id = (entry.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
        if not valid_video_id(platform, video_id):
            continue
        thumbnails = entry.get("thumbnails") or []
        duration = entry.get("duration")
        items.append({
            "id": video_id,
            "title": entry.get("title") or "",
            "duration": float(duration) if duration else None,
            "thumbnail": entry.get("thumbnail") or (thumbnails[-1].get("url") if thumbnails else None),
        })
    return items


def clean_title(title: str) -> str:
    return " ".join(re.sub(r"#\w+", " ", title or "").split())[:100]


def loudest_second(path: Path) -> float:
    """Where the audio is loudest (seconds), for the kit's boom; 0.5-s windows."""
    rate = 8000
    try:
        pcm = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
            capture_output=True, check=True, timeout=300,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return 0.0  # no audio stream (muted clip): the boom just goes at the start
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    window = rate // 2
    if len(samples) < window:
        return 0.0
    usable = samples[: len(samples) // window * window].reshape(-1, window)
    rms = np.sqrt((usable ** 2).mean(axis=1))
    return float(np.argmax(rms)) * 0.5 + 0.25


def reform_segment(duration: float, title: str, kit_id: str, peak_s: float) -> dict[str, Any]:
    """One segment covering the whole video, carrying the edit kit and the nudity/gore check."""
    return {
        "start_time": "00:00",
        "end_time": seconds_to_mmss(duration),
        "max_seconds": duration,
        "keep_ranges": [[0.0, duration]],
        "text": title,
        "hook_title": title,
        "relevance_score": 1.0,
        "reasoning": "Ready-made clip remade with the edit kit.",
        "virality_score": 0, "hook_score": 0, "engagement_score": 0, "value_score": 0, "shareability_score": 0,
        "hook_type": "reform",
        "edit_kit": {"id": kit_id, "peak_s": peak_s, "chat": []},
        "sensitive_check": True,
        # The source title over a vertical video would cover the picture; it stays the clip's name.
        "workflow_meta": {"burn_hook_title": False},
    }


async def reform_segments(task_id: str, video_path: Path, file_duration: float) -> list[dict[str, Any]] | None:
    """The whole-video segment for a Reformular task; None for any other task."""
    async with get_session_maker()() as db:
        row = (await db.execute(
            text("SELECT title, edit_kit FROM reform_items WHERE task_id = :t"), {"t": task_id},
        )).first()
    if row is None:
        return None
    peak = await asyncio.to_thread(loudest_second, video_path)
    return [reform_segment(float(file_duration), clean_title(row.title), row.edit_kit, peak)]
