"""Reformular (fork feature): ready-made clips from other channels, remade with the edit kit.

The backend records requests in ``reform_items``. Here the recorder downloads one at a
time (only while no task waits in the queue, so live windows aren't held up), covers the
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
TRANSIENT = {409, 429, 502, 503, 504}


def can_submit(queued_tasks: int, in_flight: int) -> bool:
    """One remake in the system at a time, and only while nothing waits in the queue."""
    return queued_tasks == 0 and in_flight == 0


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
        "merge_output_format": "mp4", "outtmpl": str(dest),
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])
    duration, _, (width, height) = cta._probe(dest)
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
    await collect(svc)
    await submit_next(svc)


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

    items = await svc.fetch("""
        SELECT ri.* FROM reform_items ri WHERE ri.status = 'pending' ORDER BY ri.created_at LIMIT 1
    """)
    if not items:
        return
    item = items[0]
    queued = (await svc.fetch(
        "SELECT count(*) AS n FROM tasks WHERE user_id = %s AND status IN ('pending', 'queued')", (item["user_id"],)))
    in_flight = (await svc.fetch(
        "SELECT count(*) AS n FROM reform_items ri JOIN tasks t ON t.id = ri.task_id "
        "WHERE ri.user_id = %s AND ri.status = 'submitted' AND t.status IN ('pending', 'queued', 'processing')",
        (item["user_id"],)))
    if not can_submit(queued[0]["n"] if queued else 0, in_flight[0]["n"] if in_flight else 0):
        return
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
        video_path = await svc.backend.upload(item["user_id"], upload, f"reform-upload-{item['id']}")
        task_id = await svc.backend.create_task(
            item["user_id"], video_path, task_title(item["title"]), f"reform-task-{item['id']}", "auto")
        await svc.fetch(
            "UPDATE reform_items SET status = 'submitted', task_id = %s, updated_at = now() WHERE id = %s",
            (task_id, item["id"]))
        logger.info("reform %s (%s %s) sent as task %s", item["id"], item["platform"], item["video_id"], task_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in TRANSIENT:
            logger.warning("backend busy (%s); reform %s retries later", exc.response.status_code, item["id"])
        else:
            await _fail(svc, item, f"{exc.response.status_code}: {exc.response.text[:300]}")
    except httpx.TransportError as exc:
        logger.warning("backend unreachable (%s); reform %s retries later", exc, item["id"])
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
            s.duration AS checked_duration, s.regions, s.error AS check_error
        FROM live_post_requests q
        JOIN generated_clips g ON g.id = q.clip_id
        JOIN reform_items ri ON ri.task_id = g.task_id
        JOIN live_post_targets t ON t.id = q.target_id AND t.enabled
        LEFT JOIN live_clip_sensitive s ON s.file_path = g.file_path
        WHERE t.user_id = ANY(%s)
          AND NOT EXISTS (SELECT 1 FROM live_posts p WHERE p.clip_id = g.id AND p.target_id = t.id)
        ORDER BY q.created_at LIMIT 50
    """, (allowed_user_ids,))
