"""Per-channel framing for Cat Clips v2 (fork): the webcam and gameplay boxes the owner marks on /live.

Boxes are {x, y, w, h} fractions of the source frame, so they fit any resolution. A game box alone means
the streamer has no webcam; no boxes means the automatic detector in ``streamer_split``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Tuple

from .ffmpeg import clamp_even, round_to_even

logger = logging.getLogger(__name__)

Rect = Tuple[int, int, int, int]
FRAME_WIDTH = 1280
FRAME_MAX_AGE = "30 minutes"


def box_to_rect(box: dict, width: int, height: int) -> Rect:
    """Even pixel rect of a fraction box, inside the frame."""
    w = min(round_to_even(round(box["w"] * width)), round_to_even(width))
    h = min(round_to_even(round(box["h"] * height)), round_to_even(height))
    x = clamp_even(round(box["x"] * width), 0, width - w)
    y = clamp_even(round(box["y"] * height), 0, height - h)
    return x, y, w, h


def rect_to_box(rect: Rect, width: int, height: int) -> dict:
    x, y, w, h = rect
    return {"x": round(x / width, 4), "y": round(y / height, 4), "w": round(w / width, 4), "h": round(h / height, 4)}


def channel_framing(channel_id: str, width: int, height: int) -> Optional[Tuple[Optional[Rect], Rect]]:
    """(webcam rect or None for no webcam, game rect) the owner marked, or None to use the detector."""
    from .. import sync_db

    row = sync_db.fetchrow("SELECT cam_box::text AS cam, game_box::text AS game FROM live_channels WHERE id = $1", channel_id)
    if row is None or row["game"] is None:
        return None
    cam = json.loads(row["cam"]) if row["cam"] else None
    return (box_to_rect(cam, width, height) if cam else None), box_to_rect(json.loads(row["game"]), width, height)


def save_reference_frame(input_path: Path, channel_id: str) -> None:
    """Keep a recent JPEG of the live on the channel, for the framing editor (at most every 30 min)."""
    import cv2

    from .. import sync_db

    stale = f"(framing_frame_at IS NULL OR framing_frame_at < now() - interval '{FRAME_MAX_AGE}')"
    if not sync_db.fetchval(f"SELECT {stale} FROM live_channels WHERE id = $1", channel_id):
        return
    capture = cv2.VideoCapture(str(input_path))
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) // 2))
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok:
        return
    height, width = frame.shape[:2]
    if width > FRAME_WIDTH:
        frame = cv2.resize(frame, (FRAME_WIDTH, round(height * FRAME_WIDTH / width)), interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if ok:
        # the condition again: two windows rendering at once write it only once
        sync_db.execute(
            f"UPDATE live_channels SET framing_frame = $2, framing_frame_at = now() WHERE id = $1 AND {stale}",
            channel_id, jpg.tobytes(),
        )
