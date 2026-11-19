"""The live-recorder loop. All durable state is in Postgres (live_* tables)."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import recorder
from .backend import Backend
from .chat import watch_chat
from .heat import chat_heat, chat_hints, final_score
from .windows import Chunk, Policy, decide, is_duplicate, parse_clip_time, to_wall_clock, window_chunks

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("LIVE_DATA_DIR", "/data"))
POLL_SECONDS = int(os.getenv("LIVE_POLL_SECONDS", "20"))
PROBE_SECONDS = int(os.getenv("LIVE_PROBE_SECONDS", "60"))
POLICY = Policy(
    step=timedelta(minutes=float(os.getenv("LIVE_WINDOW_STEP_MINUTES", "10"))),
    overlap=timedelta(minutes=float(os.getenv("LIVE_WINDOW_OVERLAP_MINUTES", "10"))),
)
TRANSIENT = {409, 429, 502, 503, 504}
RETRY_DELAY = timedelta(minutes=2)
MAX_RECORDINGS = max(1, int(os.getenv("LIVE_MAX_RECORDINGS", "3")))
# Live clips aim for TikTok monetization: "long" asks for 61-120 s, and a clip
# that renders shorter (pause cleanup can trim a few seconds) is dropped.
CLIP_LENGTH = os.getenv("LIVE_CLIP_LENGTH", "long")
MIN_CLIP_SECONDS = float(os.getenv("LIVE_MIN_CLIP_SECONDS", "61"))
TITLE_ZONE = ZoneInfo(os.getenv("LIVE_TIMEZONE", "UTC"))

# Channels whose owner has the live_clipping flag on (web, or admins via bypass).
CHANNELS_SQL = """
SELECT c.* FROM live_channels c
JOIN users u ON u.id = c.user_id
JOIN feature_flags f ON f.key = 'live_clipping'
WHERE c.enabled AND (f.web_enabled OR (f.admins_bypass AND u.is_admin))
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def fair_share(slots: int, owners: int) -> int:
    """Recordings one account may hold while ``owners`` accounts follow channels."""
    return max(1, -(-slots // max(owners, 1)))


class Session:
    """A channel that is live right now: its recording process and chat."""

    def __init__(self, channel: dict, recording_id: str):
        self.channel = channel
        self.recording_id = recording_id
        self.directory = DATA_DIR / recording_id
        self.recording = recorder.Recording(channel["channel"], self.directory)
        self.stop_chat = asyncio.Event()
        self.chat_task: asyncio.Task | None = None

    async def start(self, store_bucket) -> None:
        """Record, and store every 10 s of chat (spikes included) in the database."""
        await self.recording.start()

        async def on_bucket(at: datetime, chatters: int, spike: bool) -> None:
            await store_bucket(self.recording_id, at, chatters, spike)
            if spike:
                logger.info("chat spike on %s at %s", self.channel["channel"], at.isoformat())

        self.chat_task = asyncio.create_task(watch_chat(self.channel["channel"], on_bucket, self.stop_chat))

    async def stop(self) -> None:
        await self.recording.stop()
        self.stop_chat.set()
        if self.chat_task:
            self.chat_task.cancel()


class LiveService:
    def __init__(self, dsn: str, backend: Backend):
        self.dsn = dsn
        self.backend = backend
        self.sessions: dict[str, Session] = {}  # channel id -> live session
        self.last_probe: dict[str, datetime] = {}
        self.retry_after: dict[str, datetime] = {}  # recording id -> next submit attempt
        self.owners = 1  # accounts with enabled channels, refreshed each cycle

    async def run(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        async with await psycopg.AsyncConnection.connect(self.dsn, autocommit=True, row_factory=dict_row) as db:
            self.db = db
            while True:
                try:
                    await self.cycle()
                except Exception:
                    logger.exception("live cycle failed")
                await asyncio.sleep(POLL_SECONDS)

    async def cycle(self) -> None:
        channels = {c["id"]: c for c in await self.fetch(CHANNELS_SQL)}
        self.owners = len({c["user_id"] for c in channels.values()})
        for channel_id in list(self.sessions):
            if channel_id not in channels:  # disabled, deleted or flag off
                await self.end(channel_id)
        for channel in channels.values():
            await self.watch(channel)
        for recording in await self.fetch("SELECT r.*, c.user_id, c.channel, c.chat_filter, c.min_score, c.chat_weight, c.daily_window_cap FROM live_recordings r JOIN live_channels c ON c.id = r.channel_id WHERE r.ended_at IS NULL OR EXISTS (SELECT 1 FROM live_windows w WHERE w.recording_id = r.id AND w.status = 'submitted') OR r.pending_from < r.ended_at"):
            await self.advance(recording)
            await self.collect(recording)
        await self.cleanup()

    async def fetch(self, sql: str, params: dict | tuple = ()) -> list[dict]:
        async with self.db.cursor() as cur:
            await cur.execute(sql, params)
            return await cur.fetchall() if cur.description else []

    # -- going live and offline -------------------------------------------------

    async def watch(self, channel: dict) -> None:
        session = self.sessions.get(channel["id"])
        if session and session.recording.running:
            return
        last = self.last_probe.get(channel["id"])
        if session is None and last and now() - last < timedelta(seconds=PROBE_SECONDS):
            return
        self.last_probe[channel["id"]] = now()
        if session:
            logger.warning("recording of %s stopped (%s)", channel["channel"], session.recording.exit_report())
        try:
            live = await recorder.probe(channel["channel"])
        except recorder.ProbeFailed as exc:
            logger.warning("could not check %s: %s; trying again", channel["channel"], exc)
            return
        if live is None:
            if session:
                await self.end(channel["id"])
            else:
                await self.close_orphans(channel["id"])
            return
        if session:  # still live but the pipeline died: resume into the same recording
            await session.recording.start()
            return
        # Each recording is a process plus GBs of disk per hour: cap them all,
        # and give every account following channels a fair share of the slots.
        mine = sum(1 for s in self.sessions.values() if s.channel["user_id"] == channel["user_id"])
        if len(self.sessions) >= MAX_RECORDINGS or mine >= fair_share(MAX_RECORDINGS, self.owners):
            logger.warning("%s is live but no recording slot is free; waiting", channel["channel"])
            return
        open_rows = await self.fetch(
            "SELECT id FROM live_recordings WHERE channel_id = %s AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
            (channel["id"],),
        )
        if open_rows:
            recording_id = open_rows[0]["id"]
        else:
            recording_id = str(uuid.uuid4())
            await self.fetch(
                "INSERT INTO live_recordings (id, channel_id, title, started_at, pending_from) VALUES (%s, %s, %s, now(), now())",
                (recording_id, channel["id"], live.get("title")),
            )
        session = Session(channel, recording_id)
        self.sessions[channel["id"]] = session
        await session.start(self.store_bucket)

    async def store_bucket(self, recording_id: str, at: datetime, chatters: int, spike: bool) -> None:
        await self.fetch(
            "INSERT INTO live_chat_buckets (recording_id, at, chatters, spike) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (recording_id, at, chatters, spike),
        )

    async def chat_buckets(self, recording_id: str) -> list[tuple[datetime, int]]:
        rows = await self.fetch("SELECT at, chatters FROM live_chat_buckets WHERE recording_id = %s ORDER BY at", (recording_id,))
        return [(r["at"], r["chatters"]) for r in rows]

    async def end(self, channel_id: str) -> None:
        session = self.sessions.pop(channel_id, None)
        if session:
            logger.info("%s went offline; recording %s ended", session.channel["channel"], session.recording_id)
            await session.stop()
            await self.fetch("UPDATE live_recordings SET ended_at = now() WHERE id = %s AND ended_at IS NULL", (session.recording_id,))

    async def close_orphans(self, channel_id: str) -> None:
        """A recording left open by a restart, for a channel no longer live."""
        await self.fetch("UPDATE live_recordings SET ended_at = now() WHERE channel_id = %s AND ended_at IS NULL", (channel_id,))

    # -- windows ----------------------------------------------------------------

    async def advance(self, rec: dict) -> None:
        session = self.sessions.get(rec["channel_id"])
        writing = bool(session and session.recording_id == rec["id"] and session.recording.running)
        directory = DATA_DIR / rec["id"]
        chunks = await recorder.finished_chunks(directory, writing=writing) if directory.exists() else []
        ended = rec["ended_at"] is not None
        if not chunks:
            if ended:
                await self.fetch("UPDATE live_recordings SET pending_from = ended_at WHERE id = %s", (rec["id"],))
            return
        content_end = chunks[-1][1].end
        busy = bool(await self.fetch(
            "SELECT 1 FROM live_windows w JOIN live_recordings r ON r.id = w.recording_id WHERE r.channel_id = %s AND w.status = 'submitted' LIMIT 1",
            (rec["channel_id"],),
        ))
        sent = await self.fetch(
            "SELECT count(*) AS n FROM live_windows w JOIN live_recordings r ON r.id = w.recording_id WHERE r.channel_id = %s AND w.status IN ('submitted', 'done', 'error') AND w.created_at > now() - interval '1 day'",
            (rec["channel_id"],),
        )
        decision = decide(
            pending_from=rec["pending_from"], content_end=content_end, ended=ended, busy=busy,
            chat_filter=rec["chat_filter"], spikes=[r["at"] for r in await self.fetch(
                "SELECT at FROM live_chat_buckets WHERE recording_id = %s AND spike", (rec["id"],))],
            sent_last_day=sent[0]["n"], daily_cap=rec["daily_window_cap"], policy=POLICY,
        )
        if decision.action == "wait":
            if ended and not busy:  # nothing more will arrive
                await self.fetch("UPDATE live_recordings SET pending_from = %s WHERE id = %s", (rec["ended_at"], rec["id"]))
            return
        if decision.action == "submit" and self.retry_after.get(rec["id"], now()) > now():
            return
        in_window = set(window_chunks([c for _, c in chunks], decision.start, decision.end))
        picked = [(p, c) for p, c in chunks if c in in_window]
        window_id = str(uuid.uuid4())
        meta = Jsonb([{"start": c.start.isoformat(), "duration": c.duration} for _, c in picked])
        if decision.action == "skip":
            await self.fetch(
                "INSERT INTO live_windows (id, recording_id, window_start, window_end, status, reason, chunks) VALUES (%s, %s, %s, %s, 'skipped', %s, %s)",
                (window_id, rec["id"], decision.start, decision.end, decision.reason, meta),
            )
            await self.fetch("UPDATE live_recordings SET pending_from = %s WHERE id = %s", (decision.end, rec["id"]))
            return
        status, reason, task_id = await self.submit(rec, window_id, picked, decision)
        if status is None:  # transient backend trouble: retry this window later
            self.retry_after[rec["id"]] = now() + RETRY_DELAY
            return
        self.retry_after.pop(rec["id"], None)
        await self.fetch(
            "INSERT INTO live_windows (id, recording_id, window_start, window_end, status, reason, task_id, chunks) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (window_id, rec["id"], decision.start, decision.end, status, reason, task_id, meta),
        )
        await self.fetch("UPDATE live_recordings SET pending_from = %s WHERE id = %s", (decision.end, rec["id"]))

    async def submit(self, rec, window_id, picked, decision):
        window_file = DATA_DIR / rec["id"] / f"window-{window_id}.mp4"
        start, end = (t.astimezone(TITLE_ZONE).strftime("%d/%m %H:%M") for t in (decision.start, decision.end))
        title = f"{rec['channel']} live · {start}–{end[-5:]}"
        try:
            await recorder.concat([p for p, _ in picked], window_file)
            # Tell clip selection where chat exploded, on this window file's clock.
            hints = chat_hints(await self.chat_buckets(rec["id"]), [c for _, c in picked])
            video_path = await self.backend.upload(rec["user_id"], window_file, f"live-upload-{window_id}")
            task_id = await self.backend.create_task(rec["user_id"], video_path, title, f"live-task-{window_id}", CLIP_LENGTH, hints)
            logger.info("window %s of %s sent as task %s", window_id, rec["channel"], task_id)
            return "submitted", None, task_id
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in TRANSIENT:
                logger.warning("backend busy (%s) for %s; retrying", exc.response.status_code, rec["channel"])
                return None, None, None
            logger.error("window %s rejected: %s %s", window_id, exc.response.status_code, exc.response.text[:500])
            return "error", f"{exc.response.status_code}: {exc.response.text[:500]}", None
        except httpx.TransportError as exc:
            logger.warning("backend unreachable for %s: %s; retrying", rec["channel"], exc)
            return None, None, None
        except Exception as exc:
            logger.exception("window %s failed", window_id)
            return "error", str(exc)[:500], None
        finally:
            window_file.unlink(missing_ok=True)

    # -- clips ------------------------------------------------------------------

    async def collect(self, rec: dict) -> None:
        """Finished windows: drop weak and repeated clips, place the rest on the clock."""
        windows = await self.fetch(
            "SELECT w.* , t.status AS task_status FROM live_windows w JOIN tasks t ON t.id = w.task_id WHERE w.recording_id = %s AND w.status = 'submitted' AND t.status NOT IN ('pending', 'queued', 'processing')",
            (rec["id"],),
        )
        buckets = await self.chat_buckets(rec["id"]) if windows else []
        for window in windows:
            chunks = [Chunk(datetime.fromisoformat(c["start"]), c["duration"]) for c in window["chunks"]]
            kept = [(r["abs_start"], r["abs_end"]) for r in await self.fetch(
                "SELECT abs_start, abs_end FROM live_clips WHERE recording_id = %s", (rec["id"],))]
            clips = await self.fetch(
                "SELECT id, start_time, end_time, COALESCE(virality_score, 0) AS score, duration FROM generated_clips WHERE task_id = %s",
                (window["task_id"],),
            )
            # Score each clip: the AI's virality mixed with how hard chat reacted.
            for clip in clips:
                clip["span"] = (to_wall_clock(chunks, parse_clip_time(clip["start_time"])), to_wall_clock(chunks, parse_clip_time(clip["end_time"])))
                clip["heat"] = chat_heat(buckets, *clip["span"])
                clip["final"] = final_score(clip["score"], clip["heat"], rec["chat_weight"])
            dropped = 0
            for clip in sorted(clips, key=lambda c: c["final"], reverse=True):
                too_short = (clip["duration"] or 0) < MIN_CLIP_SECONDS
                if clip["final"] < rec["min_score"] or too_short or is_duplicate(clip["span"], kept):
                    await self.backend.delete_clip(rec["user_id"], window["task_id"], clip["id"])
                    dropped += 1
                    continue
                kept.append(clip["span"])
                await self.fetch(
                    "INSERT INTO live_clips (clip_id, window_id, recording_id, abs_start, abs_end, ai_score, chat_heat, final_score) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (clip["id"], window["id"], rec["id"], *clip["span"], clip["score"], clip["heat"], clip["final"]),
                )
            status = "done" if window["task_status"] == "completed" else "error"
            reason = f"kept {len(clips) - dropped} of {len(clips)} clips" if status == "done" else f"task {window['task_status']}"
            await self.fetch("UPDATE live_windows SET status = %s, reason = %s WHERE id = %s", (status, reason, window["id"]))

    async def cleanup(self) -> None:
        """Delete chunks no future window can use, and finished recordings' folders."""
        recordings = await self.fetch("SELECT id, pending_from, ended_at FROM live_recordings")
        known = {rec["id"] for rec in recordings} | {s.recording_id for s in self.sessions.values()}
        for directory in DATA_DIR.iterdir():
            if directory.is_dir() and directory.name not in known:  # channel deleted
                shutil.rmtree(directory, ignore_errors=True)
        for rec in recordings:
            directory = DATA_DIR / rec["id"]
            if not directory.exists():
                continue
            done = rec["ended_at"] is not None and rec["pending_from"] >= rec["ended_at"]
            if done and not await self.fetch("SELECT 1 FROM live_windows WHERE recording_id = %s AND status = 'submitted' LIMIT 1", (rec["id"],)):
                for path in directory.glob("*.ts"):
                    recorder.forget(path)
                shutil.rmtree(directory, ignore_errors=True)
                continue
            keep_from = rec["pending_from"] - POLICY.overlap
            for path, chunk in await recorder.finished_chunks(directory, writing=True):
                if chunk.end < keep_from:
                    recorder.forget(path)
