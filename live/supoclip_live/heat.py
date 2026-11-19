"""How hard chat reacted: per clip (chat heat) and as hints for clip selection.

Buckets are (start, distinct chatters) per 10 seconds of one broadcast, in
time order. Heat is always relative to that broadcast, so the same setting
works for a channel with 50 chatters and one with 50,000.
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import datetime, timedelta

from .windows import Chunk

LAG = timedelta(seconds=15)  # chat types what it saw 5-15 s ago
MIN_BUCKETS = 18  # 3 minutes of chat before heat means anything


def _percentile(value: float, population: list[float]) -> int:
    below = sum(1 for p in population if p < value)
    equal = sum(1 for p in population if p == value)
    return round(100 * (below + equal / 2) / len(population))


def chat_heat(buckets: list[tuple[datetime, int]], start: datetime, end: datetime) -> int | None:
    """0-100: how this clip's chat peak ranks against the broadcast.

    The clip's peak is compared with the peaks of every stretch of the same
    length, not with single buckets, or longer clips would always look hotter.
    None when there is too little chat to judge.
    """
    if len(buckets) < MIN_BUCKETS:
        return None
    counts = [c for _, c in buckets]
    inside = [c for at, c in buckets if start <= at < end + LAG]
    if not inside:
        return None
    size = min(len(inside), len(counts))
    stretch_peaks = [max(counts[i:i + size]) for i in range(len(counts) - size + 1)]
    return _percentile(max(inside), stretch_peaks)


def final_score(ai_score: int, heat: int | None, chat_weight: int) -> int:
    """Mix the AI's score with chat heat; without chat data the AI score stands."""
    if heat is None:
        return ai_score
    return round((100 - chat_weight) / 100 * ai_score + chat_weight / 100 * heat)


def to_file_time(chunks: list[Chunk], wall: datetime) -> float | None:
    """Inverse of windows.to_wall_clock: seconds into the window file, or None in a gap."""
    offset = 0.0
    for chunk in chunks:
        if chunk.start <= wall < chunk.end:
            return offset + (wall - chunk.start).total_seconds()
        offset += chunk.duration
    return None


def _label(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


def chat_hints(
    buckets: list[tuple[datetime, int]],
    chunks: list[Chunk],
    *,
    min_heat: int = 85,
    max_chars: int = 500,
) -> str | None:
    """Selection hints: where in this window's file chat exploded, hottest first.

    Times are positions in the window video, the same clock as its transcript.
    Fits the 500-character creator-instructions limit.
    """
    if len(buckets) < MIN_BUCKETS or not chunks:
        return None
    counts = sorted(c for _, c in buckets)
    peaks: list[tuple[int, float]] = []
    for at, c in buckets:
        position = to_file_time(chunks, at)
        if position is None:
            continue
        heat = round(100 * bisect_left(counts, c) / len(counts))
        if heat >= min_heat:
            peaks.append((heat, position))
    if not peaks:
        return None
    peaks.sort(key=lambda p: (-p[0], p[1]))
    picked: list[tuple[int, float]] = []
    for heat, position in peaks:  # one hint per 30 s
        if all(abs(position - other) >= 30 for _, other in picked):
            picked.append((heat, position))
    head = (
        "Live chat exploded at these points of this video (MM:SS heat/100). Chat reacts "
        "5-15 s after the cause. Strongly prefer clips that contain these moments with "
        "their setup; where chat explodes is usually the funniest or wildest part, even "
        "if little is said:"
    )
    text = head
    for heat, position in picked:
        item = f" {_label(position)} {heat},"
        if len(text) + len(item) > max_chars:
            break
        text += item
    return text.rstrip(",")
