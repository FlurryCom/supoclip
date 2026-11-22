"""Reformular (fork feature): ready-made clips from other channels, remade with the edit kit.

The backend records requests in ``reform_items``. Here the recorder downloads one at a
time (one remake in the queue per user, so the lives wait for one short clip at most), covers the
old burned-in captions of vertical videos with a black band, and submits it the same way
as a live window. When the task is done it queues the auto-publish posts, and
``post_candidates`` hands those posts to ``post_clips``.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
from pathlib import Path

import httpx

from . import cta

logger = logging.getLogger("supoclip_live.reform")

# Black band over the bottom of vertical videos, where their own captions usually sit.
# The kit's captions (position_y 0.74) and the follow CTA (~0.70) land inside it.
BAND_TOP, BAND_BOTTOM = 0.66, 0.88
TRANSIENT = {429, 502, 503, 504}  # a 409 (idempotency key clash) won't heal by retrying
MAX_SECONDS = 180  # ready-made cuts only (same cap as the backend's list)
MAX_BYTES = 500 * 1024 * 1024
RETRY_AFTER = 120.0  # seconds to wait after a busy backend before downloading again
# The oldest request of a user with no remake running: one remake at a time per user,
# queued behind the live windows already waiting, so 30 picks never jump ahead of the lives.
NEXT_PENDING_SQL = """
    SELECT ri.* FROM reform_items ri
    WHERE ri.status = 'pending'
      AND NOT EXISTS (SELECT 1 FROM reform_items o JOIN tasks t ON t.id = o.task_id
                      WHERE o.user_id = ri.user_id AND o.status = 'submitted'
                        AND t.status IN ('pending', 'queued', 'processing'))
    ORDER BY ri.created_at LIMIT 1
"""


def check_duration(duration) -> None:
    if not isinstance(duration, (int, float)) or not 0 < duration <= MAX_SECONDS:
        raise ValueError(f"vídeo com duração inválida ou acima de {MAX_SECONDS} s")


def band_filter(width: int, height: int, top: float = BAND_TOP, bottom: float = BAND_BOTTOM) -> str | None:
    if height <= width:
        return None  # horizontal clips get the live layout and have no captions of their own to hide
    y = int(height * top) // 2 * 2
    h = int(height * (bottom - top)) // 2 * 2
    return f"drawbox=x=0:y={y}:w=iw:h={h}:color=black@1:t=fill"


def download(url: str, dest: Path) -> dict:
    """The video at ``url`` as an mp4 at ``dest``; its {width, height, duration}."""
    import yt_dlp

    options = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4", "outtmpl": str(dest), "max_filesize": MAX_BYTES,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        check_duration((ydl.extract_info(url, download=False) or {}).get("duration"))  # before spending the bandwidth
        ydl.download([url])
    duration, _, (width, height) = cta._probe(dest)
    check_duration(duration)
    return {"width": width, "height": height, "duration": duration}


def cover(src: Path, dst: Path, video_filter: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", video_filter,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "copy", str(dst)],
        check=True, capture_output=True, timeout=900,
    )


def task_title(title: str) -> str:
    return " ".join(re.sub(r"#\w+", " ", title or "").split())[:100] or "Reformular"


async def cycle(svc) -> None:
    """Called every live cycle; the download and re-encode run beside it, never holding up the lives."""
    await collect(svc)
    running = getattr(svc, "reform_job", None)
    if running is None or running.done():
        svc.reform_job = asyncio.create_task(_submit_logged(svc))


async def _submit_logged(svc) -> None:
    try:
        await submit_next(svc)
    except Exception:
        logger.exception("reform submit failed")


async def collect(svc) -> None:
    """Finished remakes: mark them, and queue their posts when the owner chose auto-publish."""
    rows = await svc.fetch("""
        SELECT ri.id, ri.user_id, ri.auto_publish, ri.target_ids, ri.task_id, t.status AS task_status,
            (SELECT count(*) FROM generated_clips g WHERE g.task_id = ri.task_id) AS clips
        FROM reform_items ri JOIN tasks t ON t.id = ri.task_id
        WHERE ri.status = 'submitted' AND t.status NOT IN ('pending', 'queued', 'processing')
    """)
    for row in rows:
        if row["task_status"] != "completed" or not row["clips"]:
            await svc.fetch(
                "UPDATE reform_items SET status = 'error', reason = %s, updated_at = now() WHERE id = %s",
                (f"tarefa {row['task_status']}", row["id"]),
            )
            continue
        if row["auto_publish"] and row["target_ids"]:
            await svc.fetch("""
                INSERT INTO live_post_requests (id, user_id, clip_id, target_id)
                SELECT gen_random_uuid()::text, %s, g.id, t.id FROM generated_clips g
                JOIN live_post_targets t ON t.id = ANY(%s) AND t.user_id = %s AND t.enabled
                WHERE g.task_id = %s
                ON CONFLICT (clip_id, target_id) DO NOTHING
            """, (row["user_id"], row["target_ids"], row["user_id"], row["task_id"]))
        await svc.fetch("UPDATE reform_items SET status = 'done', reason = NULL, updated_at = now() WHERE id = %s", (row["id"],))
    # A generation deleted while it ran leaves nothing to wait for.
    await svc.fetch("UPDATE reform_items SET status = 'error', reason = 'geração apagada', updated_at = now() "
                    "WHERE status = 'submitted' AND task_id IS NULL")


async def submit_next(svc) -> None:
    from .service import DATA_DIR  # read at call time (tests point it elsewhere)

    loop = asyncio.get_running_loop()
    if loop.time() < getattr(svc, "reform_retry_at", 0.0):
        return  # the backend was busy a moment ago; don't download the same video again yet
    items = await svc.fetch(NEXT_PENDING_SQL)
    if not items:
        return
    item = items[0]
    work = DATA_DIR / f"reform-{item['id']}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        source = work / "source.mp4"
        info = await asyncio.to_thread(download, item["source_url"], source)
        video_filter = band_filter(info["width"], info["height"])
        upload = source
        if video_filter:
            upload = work / "covered.mp4"
            await asyncio.to_thread(cover, source, upload, video_filter)
        # Re-queuing a video (after an error or a deleted generation) bumps updated_at: a new attempt, new keys.
        attempt = f"{item['id']}-{int(item['updated_at'].timestamp())}"
        video_path = await svc.backend.upload(item["user_id"], upload, f"reform-upload-{attempt}")
        task_id = await svc.backend.create_task(
            item["user_id"], video_path, task_title(item["title"]), f"reform-task-{attempt}", "auto")
        await svc.fetch(
            "UPDATE reform_items SET status = 'submitted', task_id = %s, updated_at = now() WHERE id = %s",
            (task_id, item["id"]))
        logger.info("reform %s (%s %s) sent as task %s", item["id"], item["platform"], item["video_id"], task_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in TRANSIENT:
            logger.warning("backend busy (%s); reform %s retries later", exc.response.status_code, item["id"])
            svc.reform_retry_at = asyncio.get_running_loop().time() + RETRY_AFTER
        else:
            await _fail(svc, item, f"{exc.response.status_code}: {exc.response.text[:300]}")
    except httpx.TransportError as exc:
        logger.warning("backend unreachable (%s); reform %s retries later", exc, item["id"])
        svc.reform_retry_at = asyncio.get_running_loop().time() + RETRY_AFTER
    except Exception as exc:  # download or ffmpeg: the video is gone, private, or broken
        logger.warning("reform %s failed: %s", item["id"], exc)
        await _fail(svc, item, str(exc)[:300])
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def _fail(svc, item: dict, reason: str) -> None:
    await svc.fetch(
        "UPDATE reform_items SET status = 'error', reason = %s, updated_at = now() WHERE id = %s", (reason, item["id"]))


async def post_candidates(svc, allowed_user_ids: list[str]) -> list[dict]:
    """Requested posts of remade clips, shaped like post_clips' live rows."""
    return await svc.fetch("""
        SELECT g.id AS clip_id, NULL::int AS final_score, g.task_id, g.hook_title, ri.platform,
            ri.source_channel AS channel, ri.edit_kit, 'default' AS post_visibility, NULL AS live_title,
            t.id AS target_id, t.user_id, t.integration_id, t.provider, t.name, t.mode, t.visibility, t.daily_cap,
            t.blur_sensitive AS target_blur, TRUE AS channel_blur, g.created_at AS clip_created,
            t.safe_posting, t.limit_posts, t.safe_gap_minutes,
            NULL AS channel_id, FALSE AS channel_safe, NULL::int AS channel_gap_minutes,
            s.duration AS checked_duration, s.regions, s.error AS check_error, m.status AS music_status
        FROM live_post_requests q
        JOIN generated_clips g ON g.id = q.clip_id
        JOIN reform_items ri ON ri.task_id = g.task_id
        JOIN live_post_targets t ON t.id = q.target_id AND t.enabled
        LEFT JOIN live_clip_sensitive s ON s.file_path = g.file_path
        LEFT JOIN live_clip_music m ON m.file_path = g.file_path
        WHERE t.user_id = ANY(%s)
          AND NOT EXISTS (SELECT 1 FROM live_posts p WHERE p.clip_id = g.id AND p.target_id = t.id)
        ORDER BY q.created_at LIMIT 50
    """, (allowed_user_ids,))
