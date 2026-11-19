"""Twitch chat as a cheap signal: when far more people than usual talk at once.

Reads chat anonymously over IRC (no account, no token). Counts distinct
chatters per 10-second bucket, so one person or a bot spamming cannot fake a
spike, and compares each bucket against the channel's recent median.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import statistics
import unicodedata
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BUCKET = timedelta(seconds=10)
BOTS = frozenset({"nightbot", "streamelements", "moobot", "streamlabs", "fossabot", "soundalerts", "wizebot", "sery_bot"})
SAMPLE_SIZE = 20  # chat lines kept per bucket, shown on clips by edit kits
MAX_MESSAGE_CHARS = 60
# Whole words that keep a line off a posted video. Start small, extend from what shows up.
BLOCKLIST = frozenset({
    "nigger", "nigga", "faggot", "fag", "retard", "tranny", "chink", "spic", "kike", "kys",
    "viado", "bicha", "traveco", "retardado", "crioulo", "macaco", "macaca",
})
# Any "name.tld" or "name dot tld", not a fixed TLD list: viewers get creative.
_LINK = re.compile(r"https?://|www\.|\b[\w-]+\s*(?:\.|\bdot\b)\s*[a-z]{2,}\b", re.IGNORECASE)


def keep_message(text: str) -> bool:
    """Whether a chat line may be shown on a clip: no links, mentions, commands or slurs.

    Control characters and lone surrogates are refused (Postgres jsonb rejects
    some of them); lookalike letters and invisible characters are folded away
    before the checks so they can't smuggle a link or a slur through.
    """
    if any(unicodedata.category(c) in ("Cc", "Cs") for c in text):
        return False
    text = "".join(c for c in unicodedata.normalize("NFKC", text) if unicodedata.category(c) != "Cf").strip()
    if not text or len(text) > MAX_MESSAGE_CHARS or text.startswith("!") or "@" in text or _LINK.search(text):
        return False
    return not any(word in BLOCKLIST for word in re.findall(r"\w+", text.lower()))


class SpikeDetector:
    def __init__(
        self,
        *,
        history: int = 180,        # 30 minutes of buckets for the baseline
        warmup: int = 18,          # 3 minutes before any spike counts
        ratio: float = 3.0,        # bucket must be this many times the median
        min_chatters: int = 6,     # and at least this many distinct people
        cooldown: timedelta = timedelta(seconds=60),
    ):
        self.history: deque[int] = deque(maxlen=history)
        self.warmup = warmup
        self.ratio = ratio
        self.min_chatters = min_chatters
        self.cooldown = cooldown
        self.bucket_start: datetime | None = None
        self.bucket_users: set[str] = set()
        self.last_spike: datetime | None = None
        self.sample: list[str] = []  # this bucket's showable lines, one per user
        self.sampled_users: set[str] = set()
        self.closed: list[tuple[datetime, int, bool, list[str]]] = []  # (start, distinct chatters, spike, sample)

    def drain(self) -> list[tuple[datetime, int, bool, list[str]]]:
        """Buckets closed since the last call, oldest first."""
        closed, self.closed = self.closed, []
        return closed

    def add(self, user: str, message: str, at: datetime) -> datetime | None:
        """Record one chat line. Returns the spike time when a bucket closes hot."""
        spike = self._roll(at)
        user = user.lower()
        if user not in BOTS and not message.startswith("!"):
            self.bucket_users.add(user)
            if user not in self.sampled_users and len(self.sample) < SAMPLE_SIZE and keep_message(message):
                self.sampled_users.add(user)
                self.sample.append(message.strip())
        return spike

    def tick(self, at: datetime) -> datetime | None:
        """Close buckets on a quiet chat too, so silence lowers the baseline."""
        return self._roll(at)

    def _roll(self, at: datetime) -> datetime | None:
        if self.bucket_start is None:
            self.bucket_start = at
            return None
        spike = None
        while at - self.bucket_start >= BUCKET:
            count = len(self.bucket_users)
            hot = False
            if self._is_spike(count):
                when = self.bucket_start
                if self.last_spike is None or when - self.last_spike >= self.cooldown:
                    self.last_spike = when
                    spike = when
                    hot = True
            self.closed.append((self.bucket_start, count, hot, self.sample))
            self.history.append(count)
            self.bucket_users = set()
            self.sample, self.sampled_users = [], set()
            self.bucket_start += BUCKET
        return spike

    def _is_spike(self, count: int) -> bool:
        if len(self.history) < self.warmup:
            return False
        baseline = statistics.median(self.history)
        return count >= self.min_chatters and count >= self.ratio * max(baseline, 1.0)


def parse_privmsg(line: str) -> tuple[str, str] | None:
    """``:nick!nick@nick.tmi.twitch.tv PRIVMSG #chan :text`` -> (nick, text)."""
    if " PRIVMSG " not in line or not line.startswith(":"):
        return None
    prefix, _, rest = line[1:].partition(" PRIVMSG ")
    nick = prefix.split("!", 1)[0]
    _, _, text = rest.partition(" :")
    return (nick, text) if nick else None


def parse_youtube_chat_line(line: str) -> list[tuple[str, str, datetime]]:
    """(author channel id, text, sent at) for each chat message in one yt-dlp live_chat line.

    Only real messages count (text, Super Chats); banners, removals and
    engagement notices are ignored.
    """
    try:
        data = json.loads(line)
    except ValueError:
        return []
    messages = []
    for action in (data.get("replayChatItemAction") or {}).get("actions") or []:
        item = ((action.get("addChatItemAction") or {}).get("item")) or {}
        for kind in ("liveChatTextMessageRenderer", "liveChatPaidMessageRenderer"):
            renderer = item.get(kind)
            if not renderer or not renderer.get("authorExternalChannelId"):
                continue
            runs = (renderer.get("message") or {}).get("runs") or []
            text = "".join(run.get("text") or (run.get("emoji") or {}).get("shortcuts", [""])[0] for run in runs)
            try:
                at = datetime.fromtimestamp(int(renderer["timestampUsec"]) / 1_000_000, tz=timezone.utc)
            except (KeyError, ValueError):
                at = datetime.now(timezone.utc)
            messages.append((renderer["authorExternalChannelId"], text, at))
    return messages


async def watch_youtube_chat(live_url: str, on_bucket, stop: asyncio.Event, work_dir: Path) -> None:
    """Follow a YouTube live's chat through yt-dlp (which writes it as it arrives).

    Same buckets as Twitch: distinct authors per 10 s, via ``on_bucket``.
    """
    detector = SpikeDetector()
    work_dir.mkdir(parents=True, exist_ok=True)
    while not stop.is_set():
        for old in work_dir.glob("chat*"):
            old.unlink(missing_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--quiet", "--no-warnings", "--skip-download", "--write-subs", "--sub-langs", "live_chat",
            "-o", str(work_dir / "chat"), live_url,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        position, last = 0, None
        try:
            while not stop.is_set():
                # yt-dlp appends each batch of messages to this file while the live runs.
                part = next(iter(sorted(p for p in work_dir.glob("chat.live_chat.json*") if "Frag" not in p.name)), None)
                if part is not None and part.is_file():
                    with part.open("r", errors="replace") as handle:
                        handle.seek(position)
                        chunk = handle.read()
                    complete = chunk.rfind("\n") + 1  # leave a half-written line for next time
                    position += len(chunk[:complete].encode())
                    for line in chunk[:complete].splitlines():
                        for author, text, at in parse_youtube_chat_line(line):
                            last = at if last is None or at > last else last  # keep buckets moving forward
                            detector.add(author, text, last)
                detector.tick(datetime.now(timezone.utc) - timedelta(seconds=20))  # batches arrive late
                for bucket in detector.drain():
                    await on_bucket(*bucket)
                if proc.returncode is not None:
                    break
                await asyncio.sleep(2)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        if not stop.is_set():
            logger.warning("YouTube chat reader for %s stopped; restarting", live_url)
            await asyncio.sleep(15)


async def watch_chat(channel: str, on_bucket, stop: asyncio.Event) -> None:
    """Follow a channel's chat until ``stop`` is set.

    Calls ``on_bucket(start, distinct_chatters, is_spike)`` for every closed
    10-second bucket.
    """
    detector = SpikeDetector()
    while not stop.is_set():
        writer = None
        try:
            reader, writer = await asyncio.open_connection("irc.chat.twitch.tv", 6667)
            nick = f"justinfan{random.randint(10000, 99999)}"
            writer.write(f"NICK {nick}\r\nJOIN #{channel.lower()}\r\n".encode())
            await writer.drain()
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(reader.readline(), timeout=10)
                except asyncio.TimeoutError:
                    raw = None
                now = datetime.now(timezone.utc)
                if raw is None:
                    detector.tick(now)
                else:
                    if not raw:
                        raise ConnectionError("chat closed")
                    line = raw.decode(errors="replace").rstrip("\r\n")
                    if line.startswith("PING"):
                        writer.write(line.replace("PING", "PONG", 1).encode() + b"\r\n")
                        await writer.drain()
                        continue
                    parsed = parse_privmsg(line)
                    detector.add(*parsed, now) if parsed else detector.tick(now)
                for bucket in detector.drain():
                    await on_bucket(*bucket)
        except (OSError, ConnectionError) as exc:
            logger.warning("chat for %s dropped: %s; reconnecting", channel, exc)
            await asyncio.sleep(5)
        finally:
            if writer is not None:
                writer.close()
