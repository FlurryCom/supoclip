"""Per-channel framing for Cat Clips v2 (fork): the webcam and gameplay boxes the owner marks on /live.

Boxes are {x, y, w, h} fractions of the source frame, so they fit any resolution. A game box alone means
the streamer has no webcam; no boxes means the automatic detector in ``streamer_split``.

Automatic check: at the start of each live, and at most every 30 min when the local check fails (the
streamer's face is no longer in the webcam box), Claude looks at a frame with the boxes drawn. If they are
wrong and the detector's proposal is right (or the stream has no webcam), the channel's boxes change and
the owner sees a notice with "Undo".
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

from .ffmpeg import clamp_even, round_to_even

logger = logging.getLogger(__name__)

Rect = Tuple[int, int, int, int]
Face = Tuple[float, float, float, float]
FRAME_WIDTH = 1280
FRAME_MAX_AGE = "30 minutes"
CHECK_MODEL = os.getenv("FRAMING_CHECK_MODEL", "opus")
CHECKS_PER_LIVE = 12


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


def quick_check(samples: Sequence[Tuple[float, Sequence[Face]]], cam: Rect) -> bool:
    """Local, free: is the streamer's face inside the webcam box in at least half of the sampled frames?"""
    x, y, w, h = cam
    inside = sum(
        any(x <= f[0] + f[2] / 2 <= x + w and y <= f[1] + f[3] / 2 <= y + h for f in faces) for _, faces in samples
    )
    return bool(samples) and inside >= len(samples) / 2


@dataclass
class Verdict:
    normal_scene: bool
    current_ok: bool
    proposal_ok: bool
    has_webcam: bool
    reason: str
    webcam: Optional[Rect] = None  # where Claude saw the webcam, to try when the detector's proposal is bad


@dataclass
class Decision:
    kind: str  # "channel" | "proposal" | "no_cam" | "detector"
    cam: Optional[Rect]
    game: Optional[Rect]  # None only for "detector" on a channel without boxes: today's layout
    reason: str = ""


def decide(cam: Optional[Rect], game: Optional[Rect], marked: bool, auto_off: bool, passed: bool,
           propose: Callable[[], Optional[Rect]], full: Rect,
           claim: Callable[[bool], bool], verdict: Callable[[Optional[Rect]], Verdict],
           swap: Callable[[Optional[Rect], Rect, str], bool], flag: Callable[[str], None],
           release: Callable[[], None]) -> Decision:
    """What this clip uses, and what changes on the channel. Pure; the effects are injected.

    ``claim(failed)`` reserves the live's Claude check (False: not due); ``release`` puts it off for a
    while when Claude can't decide (an error, a "starting soon" screen). ``propose`` (the detector's webcam) runs only then. A swap needs Claude to reject the current boxes and
    approve the new ones, never the local check alone.
    """
    keep = Decision("channel", cam, game) if marked else Decision("detector", None, None)
    if not claim(not passed):
        return keep
    try:
        proposal = propose()
        answer = verdict(proposal)
    except Exception as exc:
        logger.warning("Framing check failed (%s); keeping the channel's framing", exc)
        release()
        return keep
    if not answer.normal_scene:
        release()  # "starting soon" or a break: look again a bit later
        return keep
    if marked and answer.current_ok:
        return keep
    target_game = game or full
    new = None
    if proposal is not None and answer.proposal_ok:
        new = Decision("proposal", proposal, target_game, answer.reason)
    elif answer.has_webcam and answer.webcam is not None:
        # the detector missed: draw the box Claude saw, and swap only if a second look confirms it
        try:
            second = verdict(answer.webcam)
        except Exception as exc:
            logger.warning("Framing re-check failed (%s)", exc)
            second = None
        if second is not None and second.proposal_ok:
            new = Decision("proposal", answer.webcam, target_game, second.reason)
    elif not answer.has_webcam:
        new = Decision("no_cam", None, target_game, answer.reason)
    if new is None:
        flag(answer.reason)
        return Decision("detector", None, game, answer.reason)
    if auto_off:  # the owner turned automatic changes off for this channel: only warn
        flag(answer.reason)
        return keep
    return new if swap(new.cam, new.game, answer.reason) else keep


VERDICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["normal_scene", "current_ok", "proposal_ok", "has_webcam", "webcam_box", "reason"],
    "properties": {
        "webcam_box": {
            "type": "object",
            "additionalProperties": False,
            "required": ["x", "y", "w", "h"],
            "properties": {k: {"type": "number"} for k in ("x", "y", "w", "h")},
        },
        "normal_scene": {"type": "boolean"},
        "current_ok": {"type": "boolean"},
        "proposal_ok": {"type": "boolean"},
        "has_webcam": {"type": "boolean"},
        "reason": {"type": "string"},
    },
}


def _prompt(current: str, proposal: bool) -> str:
    images = {
        "cam": "Image 1: the RED box is where we cut the streamer's webcam now, the BLUE box the game.",
        "no_cam": "Image 1: we treat this stream as having no webcam; the BLUE box is the game.",
        "none": "Image 1: the stream as it is (no boxes yet).",
    }[current]
    if proposal:
        images += " Image 2: the RED box is a new proposal for the webcam (same BLUE game box)."
    return f"""These are frames from a live stream (Twitch or YouTube). We make vertical clips from it: the streamer's webcam on top, the game below.
{images}

Answer:
- normal_scene: true if this is the stream's normal layout (gameplay or other content, with or without a webcam overlay). False for "starting soon", "be right back", an intermission or ad screen, or a scene that is only the camera full screen.
- current_ok: {"true if the RED box in image 1 holds the streamer's webcam overlay: the person inside, little or no game or other content in it." if current == "cam" else "true if the stream really has no webcam overlay." if current == "no_cam" else "always false."}
- proposal_ok: {"true if the RED box in image 2 holds the streamer's webcam overlay well, by the same rule." if proposal else "always false."}
- has_webcam: true if the stream shows a webcam overlay of the streamer anywhere.
- webcam_box: where that webcam overlay is, tight around its edges, as fractions of the image (x and y of the top-left corner, w and h; all between 0 and 1). All zeros if there is none.
- reason: one short sentence in Brazilian Portuguese for the channel owner, saying what you saw."""


def _drawn(frame, cam: Optional[Rect], game: Optional[Rect]) -> bytes:
    import cv2

    image = frame.copy()
    thick = max(3, image.shape[1] // 300)
    if game is not None:
        cv2.rectangle(image, game[:2], (game[0] + game[2], game[1] + game[3]), (255, 120, 0), thick)
    if cam is not None:
        cv2.rectangle(image, cam[:2], (cam[0] + cam[2], cam[1] + cam[3]), (0, 0, 255), thick)
    return _jpeg(image)


def _jpeg(frame) -> bytes:
    import cv2

    height, width = frame.shape[:2]
    if width > FRAME_WIDTH:
        frame = cv2.resize(frame, (FRAME_WIDTH, round(height * FRAME_WIDTH / width)), interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise RuntimeError("could not encode the frame")
    return jpg.tobytes()


def _middle_frame(input_path: Path):
    import cv2

    capture = cv2.VideoCapture(str(input_path))
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) // 2))
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok:
        raise RuntimeError("could not read a frame")
    return frame


def ask_claude(frame, current: str, cam: Optional[Rect], game: Optional[Rect], proposal: Optional[Rect],
               run: Callable[..., dict] | None = None) -> Verdict:
    """One Claude look (Claude Code CLI on the subscription, like the gore check) at the boxes drawn on a frame."""
    if run is None:
        from ..claude_cli import run_cli_images as run
    images = [_drawn(frame, cam, game)]
    if proposal is not None:
        images.append(_drawn(frame, proposal, game))
    # 60 s: the clip's render waits for this
    answer = run(_prompt(current, proposal is not None), images, VERDICT_SCHEMA, model=CHECK_MODEL, timeout=60)
    height, width = frame.shape[:2]
    return Verdict(answer["normal_scene"], answer["current_ok"], answer["proposal_ok"], answer["has_webcam"],
                   answer["reason"], claude_box(answer["webcam_box"], width, height))


def claude_box(box: dict, width: int, height: int) -> Optional[Rect]:
    """Claude's webcam box (fractions) as a rect clipped to the frame; None when there is none or it's tiny."""
    try:
        x0, y0, w, h = (float(box[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    x, y, right, bottom = max(x0, 0.0), max(y0, 0.0), min(x0 + w, 1.0), min(y0 + h, 1.0)
    if not (right - x >= 0.05 and bottom - y >= 0.05):  # also false for NaN
        return None
    return box_to_rect({"x": x, "y": y, "w": right - x, "h": bottom - y}, width, height)


def save_reference_frame(input_path: Path, channel_id: str) -> None:
    """Keep a recent JPEG of the live on the channel, for the framing editor (at most every 30 min)."""
    from .. import sync_db

    stale = f"(framing_frame_at IS NULL OR framing_frame_at < now() - interval '{FRAME_MAX_AGE}')"
    if not sync_db.fetchval(f"SELECT {stale} FROM live_channels WHERE id = $1", channel_id):
        return
    # the condition again: two windows rendering at once write it only once
    sync_db.execute(
        f"UPDATE live_channels SET framing_frame = $2, framing_frame_at = now() WHERE id = $1 AND {stale}",
        channel_id, _jpeg(_middle_frame(input_path)),
    )


def resolve(input_path: Path, width: int, height: int, channel_id: str, recording_id: Optional[str],
            samples: Sequence[Tuple[float, Sequence[Face]]], face: Optional[Face],
            propose: Callable[[], Optional[Rect]]) -> Decision:
    """The framing for one live clip: the channel's boxes, checked (and maybe changed) as ``decide`` says."""
    from .. import sync_db

    row = sync_db.fetchrow(
        "SELECT cam_box::text AS cam, game_box::text AS game, framing_auto AS auto FROM live_channels WHERE id = $1",
        channel_id,
    )
    if row is None or (row["game"] is None and not row["auto"]):
        return Decision("detector", None, None)  # back to automatic, with automatic changes off: today's detector
    cam_box = json.loads(row["cam"]) if row["cam"] else None
    game_box = json.loads(row["game"]) if row["game"] else None
    marked = game_box is not None
    cam = box_to_rect(cam_box, width, height) if cam_box else None
    game = box_to_rect(game_box, width, height) if marked else None
    full = (0, 0, round_to_even(width), round_to_even(height))
    # no webcam marked: fails when a steady face shows up (the streamer turned a camera on)
    passed = (quick_check(samples, cam) if cam else face is None) if marked else True
    state: dict = {}

    def claim(failed: bool) -> bool:
        if not recording_id:
            return False
        # the first check of the live; later, every 30 min while the local check fails or a check is owed;
        # at most CHECKS_PER_LIVE in all
        return sync_db.fetchrow(
            "WITH old AS (SELECT framing_checked_at AS before, framing_check_pending AS pending, "
            "framing_check_tries AS tries FROM live_recordings WHERE id = $1 FOR UPDATE) "
            "UPDATE live_recordings SET framing_checked_at = now(), framing_check_pending = false, "
            "framing_check_tries = old.tries + 1 FROM old WHERE id = $1 AND old.tries < $3 "
            "AND (old.before IS NULL OR (old.before < now() - interval '30 minutes' AND ($2 OR old.pending))) "
            "RETURNING id", recording_id, failed, CHECKS_PER_LIVE,
        ) is not None

    def verdict(proposal: Optional[Rect]) -> Verdict:
        state["frame"] = frame = _middle_frame(input_path)
        current = "cam" if cam else "no_cam" if marked else "none"
        answer = ask_claude(frame, current, cam, game, proposal)
        logger.info("Framing check for channel %s: %s", channel_id, answer)
        return answer

    def notice(sql: str, *args) -> bool:
        # the frame Claude saw becomes the editor's frame, so "Review" shows the new layout
        frame = _jpeg(state["frame"]) if "frame" in state else None
        return sync_db.execute(sql, channel_id, frame, *args) != "UPDATE 0"

    def swap(new_cam: Optional[Rect], new_game: Rect, reason: str) -> bool:
        return notice(
            # framing_previous keeps the owner's boxes from before the first automatic change, for "Undo"
            "UPDATE live_channels SET framing_previous = COALESCE(framing_previous, "
            "jsonb_build_object('cam_box', cam_box, 'game_box', game_box)), "
            "cam_box = $3::jsonb, game_box = $4::jsonb, framing_changed_at = now(), framing_notice = 'changed', "
            "framing_reason = $5, "
            "framing_frame = COALESCE($2, framing_frame), framing_frame_at = now() "
            # compare with what was read: a box the owner saved meanwhile wins
            "WHERE id = $1 AND cam_box IS NOT DISTINCT FROM $6::jsonb AND game_box IS NOT DISTINCT FROM $7::jsonb",
            json.dumps(rect_to_box(new_cam, width, height)) if new_cam else None,
            json.dumps(rect_to_box(new_game, width, height)), reason,
            row["cam"], row["game"],
        )

    def flag(reason: str) -> None:
        notice(
            "UPDATE live_channels SET framing_changed_at = now(), framing_notice = 'attention', framing_reason = $3, "
            "framing_frame = COALESCE($2, framing_frame), framing_frame_at = now() WHERE id = $1",
            reason,
        )

    def release() -> None:  # owed, and due again in 10 min
        sync_db.execute(
            "UPDATE live_recordings SET framing_check_pending = true, "
            "framing_checked_at = now() - interval '20 minutes' WHERE id = $1", recording_id,
        )

    return decide(cam, game, marked, not row["auto"], passed, propose, full, claim, verdict, swap, flag, release)
