"""Sensitive blur (fork feature): analyse a rendered live clip for nudity and heavy gore.

Runs once per clip in the worker, right after render. Nudity comes from the
NudeNet model on two frames a second; heavy gore from one Claude call on
one-frame-a-second contact sheets. The result is stored per clip file in
``live_clip_sensitive``; the recorder blurs per posting account from it.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Callable

import numpy as np
from sqlalchemy import text

from .ffmpeg import ffprobe_duration, ffprobe_video_size

logger = logging.getLogger(__name__)

WIDTH = 640  # frames are analysed at this width


def sample_frames(path: Path, fps: float) -> list[tuple[float, np.ndarray]]:
    """(second, BGR frame at 640 px wide) sampled ``fps`` times a second."""
    width, height = ffprobe_video_size(path)
    out_h = round(WIDTH * height / width / 2) * 2
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(path), "-vf", f"fps={fps},scale={WIDTH}:{out_h}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        capture_output=True, check=True, timeout=600,
    ).stdout
    size = WIDTH * out_h * 3
    return [
        (round(i / fps, 3), np.frombuffer(raw[i * size:(i + 1) * size], np.uint8).reshape(out_h, WIDTH, 3))
        for i in range(len(raw) // size)
    ]


def analyze_clip(
    path: Path,
    find_nudity: Callable[[list], list] | None = None,
    find_gore: Callable[[list], list] | None = None,
) -> dict[str, Any]:
    """{"duration", "regions", "error"}; never raises, an error just means "not checked"."""
    if find_nudity is None:
        from .nudity_check import find_nudity
    if find_gore is None:
        from .gore_check import find_gore
    regions: list[dict[str, Any]] = []
    try:
        duration = ffprobe_duration(path)
        regions += find_nudity(sample_frames(path, fps=2))
    except Exception as exc:
        return {"duration": 0.0, "regions": [], "error": f"analysis failed: {exc}"[:300]}
    try:
        regions += find_gore(sample_frames(path, fps=1))
    except Exception as exc:
        return {"duration": duration, "regions": regions, "error": f"gore check failed: {exc}"[:300]}
    return {"duration": duration, "regions": regions, "error": None}


async def save_analysis(file_path: str, result: dict[str, Any]) -> None:
    from ..database import get_session_maker

    async with get_session_maker()() as db:
        await db.execute(text("""
            INSERT INTO live_clip_sensitive (file_path, duration, regions, error, checked_at)
            VALUES (:file_path, :duration, CAST(:regions AS jsonb), :error, now())
            ON CONFLICT (file_path) DO UPDATE SET duration = EXCLUDED.duration, regions = EXCLUDED.regions,
                error = EXCLUDED.error, checked_at = now()
        """), {"file_path": file_path, "duration": result["duration"], "regions": json.dumps(result["regions"]),
               "error": result["error"]})
        await db.commit()
