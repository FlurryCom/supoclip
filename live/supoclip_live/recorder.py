"""One streamlink -> ffmpeg pipeline per live channel, cut into 60 s chunks.

Chunks are named by the wall clock when they start, so windows keep the real
broadcast time even when streamlink drops Twitch ad segments.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import signal
from datetime import datetime, timezone
from pathlib import Path

from .windows import Chunk

logger = logging.getLogger(__name__)

CHANNEL_RE = re.compile(r"^[a-z0-9_]{3,25}$")
YOUTUBE_CHANNEL_RE = re.compile(r"^(@[a-z0-9._-]{3,30}|channel/UC[A-Za-z0-9_-]{22})$")
CHUNK_FORMAT = "%Y%m%dT%H%M%SZ"
QUALITY = os.getenv("LIVE_QUALITY", "1080p60,1080p,720p60,720p,best")


def channel_url(channel: str, platform: str = "twitch") -> str:
    """Where streamlink finds the channel's current live."""
    if platform == "youtube":
        if not YOUTUBE_CHANNEL_RE.fullmatch(channel):
            raise ValueError(f"invalid YouTube channel {channel!r}")
        return f"https://www.youtube.com/{channel}/live"
    if not CHANNEL_RE.fullmatch(channel):
        raise ValueError(f"invalid Twitch channel {channel!r}")
    return f"https://www.twitch.tv/{channel}"


class ProbeFailed(Exception):
    """Could not tell whether the channel is live (network, Twitch, timeout)."""


async def probe(channel: str, platform: str = "twitch") -> dict | None:
    """Stream metadata when live, None when Twitch says offline. Anonymous.

    Raises ProbeFailed when unsure: ending a recording on a hiccup would drop it.
    """
    proc = await asyncio.create_subprocess_exec(
        "streamlink", "--json", channel_url(channel, platform),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        raise ProbeFailed("streamlink timed out") from None
    try:
        data = json.loads(out or b"{}")
    except json.JSONDecodeError:
        raise ProbeFailed("unreadable streamlink output") from None
    if data.get("streams"):
        return data.get("metadata") or {}
    if str(data.get("error", "")).startswith("No playable streams"):
        return None
    raise ProbeFailed(str(data.get("error") or "no streams and no reason")[:200])


class Recording:
    def __init__(self, channel: str, directory: Path, platform: str = "twitch"):
        self.channel = channel
        self.platform = platform
        self.directory = directory
        self.process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        pattern = str(self.directory / f"{CHUNK_FORMAT}.ts")
        # Ads: streamlink filters Twitch ad segments by default; the gap stays on
        # the wall clock through chunk names. No login token is ever used.
        command = (
            f"streamlink --stdout --retry-open 3 --stream-segment-threads 2 "
            f"{shlex.quote(channel_url(self.channel, self.platform))} {shlex.quote(QUALITY)} "
            f"| ffmpeg -hide_banner -loglevel error -i pipe:0 -map 0:v:0 -map 0:a:0? -c copy "
            f"-f segment -segment_time 60 -reset_timestamps 1 -strftime 1 {shlex.quote(pattern)}"
        )
        log = (self.directory / "recorder.log").open("ab")
        self.process = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.DEVNULL, stderr=log, start_new_session=True,
        )
        log.close()
        logger.info("recording %s into %s", self.channel, self.directory)

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def exit_report(self) -> str:
        """Exit code and the last lines streamlink/ffmpeg wrote, for the logs."""
        code = self.process.returncode if self.process else None
        try:
            tail = (self.directory / "recorder.log").read_text(errors="replace").strip().splitlines()[-5:]
        except OSError:
            tail = []
        return f"exit {code}: " + " | ".join(tail)

    async def stop(self) -> None:
        if not self.running:
            return
        try:
            os.killpg(self.process.pid, signal.SIGINT)  # ffmpeg closes the last chunk cleanly
            await asyncio.wait_for(self.process.wait(), timeout=20)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


_durations: dict[Path, float] = {}


async def _duration(path: Path) -> float:
    if path not in _durations:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        try:
            _durations[path] = float(out.strip() or 0)
        except ValueError:
            _durations[path] = 0.0
    return _durations[path]


def _chunk_start(path: Path) -> datetime:
    return datetime.strptime(path.stem, CHUNK_FORMAT).replace(tzinfo=timezone.utc)


async def finished_chunks(directory: Path, *, writing: bool) -> list[tuple[Path, Chunk]]:
    """Chunks ffmpeg is done with. While recording, the newest is still open."""
    paths = sorted(directory.glob("*.ts"))
    if writing and paths:
        paths = paths[:-1]
    chunks = []
    for path in paths:
        duration = await _duration(path)
        if duration > 0:
            chunks.append((path, Chunk(_chunk_start(path), duration)))
    return chunks


def forget(path: Path) -> None:
    _durations.pop(path, None)
    path.unlink(missing_ok=True)


async def concat(paths: list[Path], dest: Path) -> None:
    listing = dest.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in paths))
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
        "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(dest),
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    listing.unlink(missing_ok=True)
    if proc.returncode != 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"concat failed: {err.decode(errors='replace')[-500:]}")
