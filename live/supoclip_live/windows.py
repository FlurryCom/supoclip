"""Pure decisions for live clipping: which window to send, and where clips sit.

Times are broadcast wall-clock datetimes. A window's video is its chunks
concatenated, so a clip time inside the file maps back to a chunk's wall
start. Twitch ad breaks that streamlink filters out leave gaps between chunks;
mapping through chunks keeps clips on the real clock regardless.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Chunk:
    start: datetime
    duration: float  # seconds of video in the file

    @property
    def end(self) -> datetime:
        return self.start + timedelta(seconds=self.duration)


@dataclass(frozen=True)
class Policy:
    step: timedelta = timedelta(minutes=10)      # new content before a window is due
    overlap: timedelta = timedelta(minutes=10)   # context repeated from before
    max_window: timedelta = timedelta(minutes=40)
    min_final: timedelta = timedelta(minutes=3)  # shortest tail sent when the stream ends
    spike_tail: timedelta = timedelta(minutes=2)  # after-context a spike needs


@dataclass(frozen=True)
class Decision:
    action: str  # "wait", "skip", "submit"
    start: datetime | None = None
    end: datetime | None = None
    reason: str = ""


def decide(
    *,
    pending_from: datetime,
    content_end: datetime,
    ended: bool,
    busy: bool,
    chat_filter: bool,
    spikes: list[datetime],
    sent_last_day: int,
    daily_cap: int,
    policy: Policy = Policy(),
) -> Decision:
    """What to do with the content recorded after ``pending_from``.

    ``busy`` means this channel already has a window in the pipeline. Content
    then keeps accumulating, so the next window covers it all (merge), capped
    at ``max_window``; the oldest part beyond the cap is dropped.
    """
    new = content_end - pending_from
    due = new >= policy.step or (ended and new >= policy.min_final)
    if not due:
        return Decision("wait")
    if busy and not ended:
        return Decision("wait", reason="channel busy")
    start = max(pending_from - policy.overlap, content_end - policy.max_window)
    if chat_filter:
        # Only spikes in the new part count: one in the overlap was already
        # judged with the previous window, and would just repeat it.
        hot = [s for s in spikes if pending_from <= s < content_end]
        if not hot:
            return Decision("skip", start, content_end, "no chat spike")
        # A spike at the very end needs its aftermath in the same window.
        if (
            not ended
            and content_end - max(hot) < policy.spike_tail
            and content_end - start < policy.max_window
        ):
            return Decision("wait", reason="spike needs after-context")
    if sent_last_day >= daily_cap:
        return Decision("skip", start, content_end, "daily window cap reached")
    return Decision("submit", start, content_end)


def window_chunks(chunks: list[Chunk], start: datetime, end: datetime) -> list[Chunk]:
    """Whole chunks that overlap [start, end), in order."""
    return [c for c in chunks if c.end > start and c.start < end]


def to_wall_clock(chunks: list[Chunk], seconds: float) -> datetime:
    """Map a time in the concatenated window file to broadcast wall clock."""
    if not chunks:
        raise ValueError("window has no chunks")
    offset = 0.0
    for chunk in chunks:
        if seconds < offset + chunk.duration:
            return chunk.start + timedelta(seconds=max(0.0, seconds - offset))
        offset += chunk.duration
    last = chunks[-1]
    return last.end


def parse_clip_time(value: str) -> float:
    """``MM:SS``, ``HH:MM:SS`` or plain seconds, as the clip table stores them."""
    parts = [float(p) for p in str(value).strip().split(":")]
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def overlap_ratio(a: tuple[datetime, datetime], b: tuple[datetime, datetime]) -> float:
    """Shared time over the shorter clip's length."""
    shared = (min(a[1], b[1]) - max(a[0], b[0])).total_seconds()
    shortest = min((a[1] - a[0]).total_seconds(), (b[1] - b[0]).total_seconds())
    if shared <= 0 or shortest <= 0:
        return 0.0
    return shared / shortest


def is_duplicate(
    clip: tuple[datetime, datetime], kept: list[tuple[datetime, datetime]], threshold: float = 0.5
) -> bool:
    return any(overlap_ratio(clip, other) > threshold for other in kept)
