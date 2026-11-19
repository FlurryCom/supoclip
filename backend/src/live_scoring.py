"""Live clipping (fork feature): score live-window clips before they are rendered.

The live recorder sends stretches of a broadcast as tasks. Rendering a clip
that will then be dropped for its score wastes minutes of CPU, so the pipeline
asks here first: each selected segment's final score (the AI's virality mixed
with how hard chat reacted) must reach the channel's minimum score.

The math mirrors live/supoclip_live/heat.py and windows.py, which score the
clips again after rendering; keep both in step.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from .database import get_session_maker

logger = logging.getLogger(__name__)

LAG = timedelta(seconds=15)  # chat reacts 5-15 s after the moment
MIN_BUCKETS = 18


class NothingAboveScore(Exception):
    """No selected moment of a live window reached the channel's minimum score."""


def to_wall_clock(chunks: list[tuple[datetime, float]], seconds: float) -> datetime:
    offset = 0.0
    for start, duration in chunks:
        if seconds < offset + duration:
            return start + timedelta(seconds=max(0.0, seconds - offset))
        offset += duration
    start, duration = chunks[-1]
    return start + timedelta(seconds=duration)


def parse_clip_time(value: Any) -> float:
    seconds = 0.0
    for part in str(value).strip().split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def chat_heat(buckets: list[tuple[datetime, int]], start: datetime, end: datetime) -> int | None:
    if len(buckets) < MIN_BUCKETS:
        return None
    counts = [c for _, c in buckets]
    inside = [c for at, c in buckets if start <= at < end + LAG]
    if not inside:
        return None
    size = min(len(inside), len(counts))
    peaks = [max(counts[i:i + size]) for i in range(len(counts) - size + 1)]
    value = max(inside)
    below = sum(1 for p in peaks if p < value)
    equal = sum(1 for p in peaks if p == value)
    return round(100 * (below + equal / 2) / len(peaks))


def final_score(ai_score: int, heat: int | None, chat_weight: int) -> int:
    if heat is None:
        return ai_score
    return round((100 - chat_weight) / 100 * ai_score + chat_weight / 100 * heat)


def keep_segments(
    segments: list[dict[str, Any]],
    chunks: list[tuple[datetime, float]],
    buckets: list[tuple[datetime, int]],
    min_score: int,
    chat_weight: int,
) -> list[dict[str, Any]]:
    """Segments whose final score reaches ``min_score``, best first."""
    scored = []
    for segment in segments:
        try:
            start = to_wall_clock(chunks, parse_clip_time(segment["start_time"]))
            end = to_wall_clock(chunks, parse_clip_time(segment["end_time"]))
        except (KeyError, ValueError, IndexError):
            scored.append((int(segment.get("virality_score") or 0), segment))  # can't place it: AI score alone
            continue
        ai = int(segment.get("virality_score") or 0)
        scored.append((final_score(ai, chat_heat(buckets, start, end), chat_weight), segment))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [segment for score, segment in scored if score >= min_score]


async def gate_live_segments(task_id: str, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only the segments worth rendering, for a live window's task; others pass unchanged.

    Raises NothingAboveScore when a live window has no segment that qualifies.
    """
    async with get_session_maker()() as db:
        row = (await db.execute(text("""
            SELECT w.chunks, w.recording_id, c.min_score, c.chat_weight
            FROM live_windows w
            JOIN live_recordings r ON r.id = w.recording_id
            JOIN live_channels c ON c.id = r.channel_id
            WHERE w.task_id = :task_id
        """), {"task_id": task_id})).first()
        if row is None:
            return segments  # not a live window
        buckets = [(b.at, b.chatters) for b in (await db.execute(text(
            "SELECT at, chatters FROM live_chat_buckets WHERE recording_id = :id ORDER BY at"
        ), {"id": row.recording_id})).all()]
    chunks = [(datetime.fromisoformat(c["start"]), float(c["duration"])) for c in row.chunks or []]
    if not chunks:
        return segments
    kept = keep_segments(segments, chunks, buckets, row.min_score, row.chat_weight)
    logger.info("Live window %s: %d of %d moments reach score %d", task_id, len(kept), len(segments), row.min_score)
    if not kept:
        raise NothingAboveScore(f"No moment reached the minimum score of {row.min_score}")
    return kept
