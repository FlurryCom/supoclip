"""The live-recorder loop. All durable state is in Postgres (live_* tables)."""
from __future__ import annotations

import asyncio
import json
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

from . import cta, postiz, recorder, reform, sensitive
from .backend import Backend
from .chat import watch_chat, watch_youtube_chat
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
# Postiz's public API allows ~30 calls/hour, so posts are re-checked sparingly.
CONFIRM_EVERY = timedelta(minutes=10)
CONFIRM_WINDOW = timedelta(hours=2)
MAX_RECORDINGS = max(1, int(os.getenv("LIVE_MAX_RECORDINGS", "3")))
MIN_FREE_GB = float(os.getenv("LIVE_MIN_FREE_GB", "10"))  # don't start recordings below this
# Below this, running recordings stop too: a full disk took Postgres down once (2026-11-19).
STOP_FREE_GB = float(os.getenv("LIVE_STOP_FREE_GB", str(MIN_FREE_GB / 2)))
MAX_BACKLOG = timedelta(minutes=20)  # older unprocessed live is skipped instead of piling up on disk (owner, 2026-11-20)


def skip_backlog(pending_from: datetime, content_end: datetime) -> datetime | None:
    """Where to restart a live that fell more than MAX_BACKLOG behind; None if it is within it."""
    return content_end - MAX_BACKLOG if content_end - pending_from > MAX_BACKLOG else None
# One Postiz key serves the server: only these SupoClip user ids may post through it.
POSTIZ_ALLOWED_USER_IDS = [i.strip() for i in os.getenv("POSTIZ_ALLOWED_USER_IDS", "").split(",") if i.strip()]
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
        self.recording = recorder.Recording(channel["channel"], self.directory, channel["platform"])
        self.stop_chat = asyncio.Event()
        self.chat_task: asyncio.Task | None = None

    async def start(self, store_bucket) -> None:
        """Record, and store every 10 s of chat (spikes included) in the database."""
        await self.recording.start()

        store = self.safe_store(store_bucket, self.recording_id, self.channel["channel"])

        async def on_bucket(at: datetime, chatters: int, spike: bool, messages: list[str]) -> None:
            await store(at, chatters, spike, messages)
            if spike:
                logger.info("chat spike on %s at %s", self.channel["channel"], at.isoformat())

        if self.channel["platform"] == "youtube":
            url = recorder.channel_url(self.channel["channel"], "youtube")
            reader = watch_youtube_chat(url, on_bucket, self.stop_chat, self.directory / "chat")
        else:
            reader = watch_chat(self.channel["channel"], on_bucket, self.stop_chat)
        self.chat_task = asyncio.create_task(reader)

    @staticmethod
    def safe_store(store_bucket, recording_id: str, channel: str):
        """``store_bucket`` for one recording that logs failures, so a bad bucket can't end the chat reader."""
        async def store(at: datetime, chatters: int, spike: bool, messages: list[str]) -> None:
            try:
                await store_bucket(recording_id, at, chatters, spike, messages)
            except Exception:
                logger.exception("could not store a chat bucket for %s", channel)
        return store

    async def stop(self) -> None:
        await self.recording.stop()
        self.stop_chat.set()
        if self.chat_task:
            self.chat_task.cancel()


def processing_threshold(channel_min: int, account_min: int | None) -> int:
    """Same rule as backend/src/live_scoring.py: the posting accounts' lowest processing score wins."""
    return account_min if account_min is not None else channel_min


NO_WINDOW_LIMIT = 1_000_000


def window_cap(channel: dict) -> int:
    """Windows a channel may send per day; the owner can switch the limit off per channel."""
    return channel["daily_window_cap"] if channel.get("limit_windows", True) else NO_WINDOW_LIMIT


def safe_to_post(safe_posting: bool, last_posted: datetime | None, now: datetime, minutes: int) -> bool:
    """Safe posting spaces posts by the owner's chosen minutes so they never go out in a burst."""
    return not safe_posting or last_posted is None or now - last_posted >= timedelta(minutes=minutes)


class LiveService:
    def __init__(self, dsn: str, backend: Backend, postiz_client: "postiz.Postiz | None" = None):
        self.dsn = dsn
        self.backend = backend
        self.postiz = postiz_client  # None: posting is off
        self.sessions: dict[str, Session] = {}  # channel id -> live session
        self.last_probe: dict[str, datetime] = {}
        self.retry_after: dict[str, datetime] = {}  # recording id -> next submit attempt
        self.owners = 1  # accounts with enabled channels, refreshed each cycle
        self.last_confirm = datetime.min.replace(tzinfo=timezone.utc)

    async def run(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.db = None
        while True:
            try:
                if self.db is None or self.db.closed:  # first run, or Postgres restarted under us
                    self.db = await psycopg.AsyncConnection.connect(self.dsn, autocommit=True, row_factory=dict_row)
                await self.cycle()
            except Exception:
                logger.exception("live cycle failed")
            await asyncio.sleep(POLL_SECONDS)

    async def stop_if_disk_low(self) -> None:
        free_gb = shutil.disk_usage(DATA_DIR).free / 2**30
        if free_gb >= STOP_FREE_GB or not self.sessions:
            return
        logger.warning("only %.1f GB free (stop line %.0f GB); stopping %d recordings", free_gb, STOP_FREE_GB, len(self.sessions))
        for channel_id in list(self.sessions):
            await self.end(channel_id)

    async def cycle(self) -> None:
        await self.stop_if_disk_low()
        channels = {c["id"]: c for c in await self.fetch(CHANNELS_SQL)}
        self.owners = len({c["user_id"] for c in channels.values()})
        for channel_id in list(self.sessions):
            if channel_id not in channels:  # disabled, deleted or flag off
                await self.end(channel_id)
        for channel in channels.values():
            await self.watch(channel)
        for recording in await self.fetch("SELECT r.*, c.user_id, c.platform, c.channel, c.chat_filter, c.min_score, c.chat_weight, c.daily_window_cap, c.limit_windows, (SELECT min(t.process_min_score) FROM live_post_targets t WHERE t.user_id = c.user_id AND t.enabled) AS account_min FROM live_recordings r JOIN live_channels c ON c.id = r.channel_id WHERE r.ended_at IS NULL OR EXISTS (SELECT 1 FROM live_windows w WHERE w.recording_id = r.id AND w.status = 'submitted') OR r.pending_from < r.ended_at"):
            await self.advance(recording)
            await self.collect(recording)
        try:
            await reform.cycle(self)  # Reformular: one ready-made clip at a time, after the lives
        except Exception:
            logger.exception("reform step failed; lives carry on")
        await self.post_clips()
        await self.confirm_posts()
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
            live = await recorder.probe(channel["channel"], channel["platform"])
        except recorder.ProbeFailed as exc:
            logger.warning("could not check %s: %s; trying again", channel["channel"], exc)
            return
        if live is None:
            await self.fetch("UPDATE live_channels SET waiting_since = NULL WHERE id = %s", (channel["id"],))
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
            await self.mark_waiting(channel["id"])
            return
        free_gb = shutil.disk_usage(DATA_DIR).free / 2**30
        if free_gb < MIN_FREE_GB:  # a full disk would break every recording and the app
            logger.warning("%s is live but only %.1f GB is free (minimum %.0f); not recording", channel["channel"], free_gb, MIN_FREE_GB)
            await self.mark_waiting(channel["id"])
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
        await self.fetch("UPDATE live_channels SET waiting_since = NULL WHERE id = %s", (channel["id"],))
        await session.start(self.store_bucket)

    async def mark_waiting(self, channel_id: str) -> None:
        """Live but not recorded yet (no free slot or disk): the Live page shows it as waiting."""
        await self.fetch("UPDATE live_channels SET waiting_since = coalesce(waiting_since, now()) WHERE id = %s", (channel_id,))

    async def store_bucket(self, recording_id: str, at: datetime, chatters: int, spike: bool, messages: list[str]) -> None:
        await self.fetch(
            "INSERT INTO live_chat_buckets (recording_id, at, chatters, spike, messages) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (recording_id, at, chatters, spike, json.dumps(messages)),
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
        caught_up = skip_backlog(rec["pending_from"], content_end)
        if caught_up is not None:
            logger.warning("%s is %s behind; skipping to the last hour", rec["channel"], content_end - rec["pending_from"])
            await self.fetch("UPDATE live_recordings SET pending_from = %s WHERE id = %s", (caught_up, rec["id"]))
            rec["pending_from"] = caught_up
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
            sent_last_day=sent[0]["n"], daily_cap=window_cap(rec), policy=POLICY,
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
                if clip["final"] < processing_threshold(rec["min_score"], rec.get("account_min")) or too_short or is_duplicate(clip["span"], kept):
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

    async def post_clips(self) -> None:
        """Send kept clips to the Postiz accounts the user authorized, at most once each.

        Only clips made after an account was authorized go to it, so authorizing
        an account never floods it with old clips; clips the owner publishes by
        hand from the Library (live_post_requests) skip that and the minimum score.
        """
        if self.postiz is None:
            return
        candidates = await self.fetch("""
            SELECT lc.clip_id, lc.final_score, g.task_id, g.hook_title, c.platform, c.channel, c.edit_kit, c.post_visibility, r.title AS live_title,
                t.id AS target_id, t.user_id, t.integration_id, t.provider, t.name, t.mode, t.visibility, t.daily_cap,
                t.blur_sensitive AS target_blur, c.blur_sensitive AS channel_blur, g.created_at AS clip_created, t.safe_posting, t.limit_posts, t.safe_gap_minutes,
                c.id AS channel_id, c.safe_posting AS channel_safe, c.safe_gap_minutes AS channel_gap_minutes,
                s.duration AS checked_duration, s.regions, s.error AS check_error, m.status AS music_status
            FROM live_clips lc
            JOIN generated_clips g ON g.id = lc.clip_id
            LEFT JOIN live_clip_sensitive s ON s.file_path = g.file_path
            LEFT JOIN live_clip_music m ON m.file_path = g.file_path
            JOIN live_recordings r ON r.id = lc.recording_id
            JOIN live_channels c ON c.id = r.channel_id
            JOIN live_post_targets t ON t.user_id = c.user_id AND t.enabled
            LEFT JOIN live_post_requests q ON q.clip_id = lc.clip_id AND q.target_id = t.id
            WHERE t.user_id = ANY(%s)
              AND NOT EXISTS (SELECT 1 FROM live_posts p WHERE p.clip_id = lc.clip_id AND p.target_id = t.id)
              AND (q.id IS NOT NULL  -- published by hand from the Library: any score, any age
                   OR (lc.final_score >= t.min_score AND g.created_at >= t.created_at
                       AND g.created_at > now() - interval '1 day'))
            ORDER BY q.created_at NULLS LAST, lc.final_score DESC LIMIT 50
        """, (POSTIZ_ALLOWED_USER_IDS,))
        # Remade clips (Reformular) the owner asked to publish go first, through the same steps.
        candidates = await reform.post_candidates(self, POSTIZ_ALLOWED_USER_IDS) + candidates
        if not candidates:
            return
        try:
            integrations = await self.postiz.integrations()
        except httpx.HTTPError as exc:
            logger.warning("Postiz unreachable (%s); posting later", exc)
            return
        marks = await self.fetch("SELECT at FROM live_feature_marks WHERE name = 'sensitive_blur'")
        blur_since = marks[0]["at"] if marks else datetime.min.replace(tzinfo=timezone.utc)
        for row in candidates:
            target = {k: row[k] for k in ("integration_id", "provider", "name", "mode")}
            target["visibility"] = postiz.effective_visibility(row["visibility"], row["post_visibility"])
            reason = postiz.refusal_reason(target, integrations) or sensitive.music_refusal(row)
            if reason:
                logger.warning("not posting clip %s to %s: %s", row["clip_id"], row["name"], reason)
                await self.record_post(row, "refused", None, reason)
                continue
            recent = (await self.fetch(
                "SELECT count(*) AS n, max(created_at) AS last_posted FROM live_posts "
                "WHERE target_id = %s AND status = 'posted' AND created_at > now() - interval '1 day'",
                (row["target_id"],),
            ))[0]
            if row.get("limit_posts") and recent["n"] >= row["daily_cap"]:
                continue  # owner's daily limit for this account; stays a candidate until the 24 h window frees up
            now_ = datetime.now(timezone.utc)
            if not safe_to_post(row.get("safe_posting", True), recent.get("last_posted"), now_, row.get("safe_gap_minutes") or 15):
                continue  # safe posting: this account waits its minutes between posts; the clip stays queued
            if row.get("channel_safe"):
                channel_last = (await self.fetch(
                    "SELECT max(p.created_at) AS channel_last FROM live_posts p "
                    "JOIN live_clips lc ON lc.clip_id = p.clip_id JOIN live_recordings r ON r.id = lc.recording_id "
                    "WHERE p.target_id = %s AND p.status = 'posted' AND r.channel_id = %s",
                    (row["target_id"], row.get("channel_id")),
                ))[0]["channel_last"]
                if not safe_to_post(True, channel_last, now_, row.get("channel_gap_minutes") or 15):
                    continue  # this live channel's own gap on this account
            clip_file = DATA_DIR / f"post-{row['clip_id']}.mp4"
            upload_file = clip_file
            try:
                await self.backend.download_clip(row["user_id"], row["task_id"], row["clip_id"], clip_file)
                # Nudity and gore: blur for this account, or refuse when it must be blurred and can't be.
                actual = (await asyncio.to_thread(cta._probe, clip_file))[0]
                action, why, regions = sensitive.decide(row, blur_since, actual)
                if action == "refuse":
                    logger.warning("not posting clip %s to %s: %s", row["clip_id"], row["name"], why)
                    await self.record_post(row, "refused", None, why)
                    continue
                # The channel's edit kit adds this platform's follow CTA on the tail.
                try:
                    upload_file = await asyncio.to_thread(
                        cta.finish_clip, clip_file, row["edit_kit"], row["provider"], regions, action == "blur")
                except cta.BlurFailed as exc:
                    logger.warning("not posting clip %s to %s: blur failed (%s)", row["clip_id"], row["name"], exc)
                    await self.record_post(row, "refused", None, "falha ao borrar")
                    continue
                media = await self.postiz.upload(upload_file)
                title = row["hook_title"] or f"{row['channel']} live"
                body = postiz.build_post(target, media, title, postiz.caption(title, row["channel"], row["live_title"], row["platform"]))
                post_id = await self.postiz.create_post(body)
                logger.info("clip %s posted to %s %s as %s (%s)", row["clip_id"], row["provider"], row["name"], row["mode"], post_id)
                await self.record_post(row, "posted", post_id, row["mode"])
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500 or exc.response.status_code == 429:
                    logger.warning("Postiz busy (%s); posting clip %s later", exc.response.status_code, row["clip_id"])
                else:
                    await self.record_post(row, "error", None, f"{exc.response.status_code}: {exc.response.text[:300]}")
            except httpx.TransportError as exc:
                logger.warning("Postiz unreachable (%s); posting clip %s later", exc, row["clip_id"])
            except Exception as exc:
                logger.exception("posting clip %s failed", row["clip_id"])
                await self.record_post(row, "error", None, str(exc)[:300])
            finally:
                clip_file.unlink(missing_ok=True)
                upload_file.unlink(missing_ok=True)

    async def confirm_posts(self) -> None:
        """Postiz accepts a post before the platform does. Posts it later marks ERROR become
        errors here too, so they don't count as posted (nor against the daily cap)."""
        if self.postiz is None or now() - self.last_confirm < CONFIRM_EVERY:
            return
        recent = await self.fetch(
            "SELECT id, postiz_post_id FROM live_posts WHERE status = 'posted' AND detail = 'now' "
            "AND postiz_post_id <> '' AND created_at > now() - %s", (CONFIRM_WINDOW,),
        )
        if not recent:
            return
        self.last_confirm = now()
        try:
            states = await self.postiz.states(now() - CONFIRM_WINDOW - timedelta(hours=1))
        except httpx.HTTPError as exc:
            logger.warning("could not check Postiz post states (%s); trying later", exc)
            return
        for post in recent:
            if states.get(post["postiz_post_id"]) == "ERROR":
                await self.fetch(
                    "UPDATE live_posts SET status = 'error', detail = %s WHERE id = %s",
                    ("Postiz could not publish this post; see the account in Postiz", post["id"]),
                )

    async def record_post(self, row: dict, status: str, post_id: str | None, detail: str | None) -> None:
        await self.fetch(
            "INSERT INTO live_posts (id, clip_id, target_id, status, postiz_post_id, detail) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (str(uuid.uuid4()), row["clip_id"], row["target_id"], status, post_id, detail),
        )

    async def release_sources(self) -> None:
        """Ask the backend to delete finished windows' uploaded copies (1-2 GB each)."""
        rows = await self.fetch(
            "SELECT w.id, c.user_id FROM live_windows w JOIN live_recordings r ON r.id = w.recording_id "
            "JOIN live_channels c ON c.id = r.channel_id "
            "WHERE w.status IN ('done', 'error') AND w.task_id IS NOT NULL AND NOT w.source_released LIMIT 10"
        )
        for row in rows:
            try:
                await self.backend.release_source(row["user_id"], row["id"])
            except httpx.HTTPError as exc:
                logger.warning("could not release window %s source: %s", row["id"], exc)

    async def cleanup(self) -> None:
        """Delete chunks no future window can use, and finished recordings' folders."""
        await self.release_sources()
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
