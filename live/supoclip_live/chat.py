"""Twitch chat as a cheap signal: when far more people than usual talk at once.

Reads chat anonymously over IRC (no account, no token). Counts distinct
chatters per 10-second bucket, so one person or a bot spamming cannot fake a
spike, and compares each bucket against the channel's recent median.
"""
from __future__ import annotations

import asyncio
import logging
import random
import statistics
from collections import deque
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

BUCKET = timedelta(seconds=10)
BOTS = frozenset({"nightbot", "streamelements", "moobot", "streamlabs", "fossabot", "soundalerts", "wizebot", "sery_bot"})


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

    def add(self, user: str, message: str, at: datetime) -> datetime | None:
        """Record one chat line. Returns the spike time when a bucket closes hot."""
        spike = self._roll(at)
        user = user.lower()
        if user not in BOTS and not message.startswith("!"):
            self.bucket_users.add(user)
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
            if self._is_spike(count):
                when = self.bucket_start
                if self.last_spike is None or when - self.last_spike >= self.cooldown:
                    self.last_spike = when
                    spike = when
            self.history.append(count)
            self.bucket_users = set()
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


async def watch_chat(channel: str, on_spike, stop: asyncio.Event) -> None:
    """Follow a channel's chat until ``stop`` is set; call ``on_spike(datetime)``."""
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
                    spike = detector.tick(now)
                else:
                    if not raw:
                        raise ConnectionError("chat closed")
                    line = raw.decode(errors="replace").rstrip("\r\n")
                    if line.startswith("PING"):
                        writer.write(line.replace("PING", "PONG", 1).encode() + b"\r\n")
                        await writer.drain()
                        continue
                    parsed = parse_privmsg(line)
                    spike = detector.add(*parsed, now) if parsed else detector.tick(now)
                if spike is not None:
                    await on_spike(spike)
        except (OSError, ConnectionError) as exc:
            logger.warning("chat for %s dropped: %s; reconnecting", channel, exc)
            await asyncio.sleep(5)
        finally:
            if writer is not None:
                writer.close()
