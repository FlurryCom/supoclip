"""Cat Clips v2 layout (fork): streamer on top, title on a yellow bar, gameplay below.

1080x1920 output: the webcam crop fills the top 720 px, the clip title sits on a
200 px yellow bar, and the gameplay fills the bottom 1000 px with the captions at
its top. The webcam is found as a face that stays put through the clip, in any
part of the frame. Without one, the top shows the gameplay blurred.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence, Tuple

import numpy as np

from ..font_registry import FONTS_DIR
from .ffmpeg import clamp_even, round_to_even

logger = logging.getLogger(__name__)

Box = Tuple[float, float, float, float]
TOP_H, BAR_H, GAME_H = 720, 200, 1000
YELLOW, BLACK, WHITE = "0xFEC803", "0x111111", "0xFFFFFF"  # owner: white title with a black border
TITLE_FONT = FONTS_DIR / "ArchivoBlack-Regular.ttf"
MAX_TITLE_PX = 64
# Caption centre at the top of the gameplay, with a visible gap under the bar (owner, 2026-11-20).
CAPTION_Y = (TOP_H + BAR_H + 100) / 1920


def find_streamer_cam(width: int, height: int, samples: Sequence[Tuple[float, Sequence[Box]]]) -> Optional[Box]:
    """The webcam face: a face that stays in one place in at least half of the sampled frames."""
    if len(samples) < 6:
        return None
    faces = [f for _, fs in samples for f in fs if f[3] < 0.35 * height]
    reach = 0.06 * width
    best: list[Box] = []
    for face in faces:
        cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
        near = []
        for _, fs in samples:
            hit = next((f for f in fs if abs(f[0] + f[2] / 2 - cx) <= reach and abs(f[1] + f[3] / 2 - cy) <= reach), None)
            if hit:
                near.append(hit)
        if len(near) > len(best):
            best = near
    if len(best) < 0.5 * len(samples):
        return None
    return tuple(float(np.median([f[i] for f in best])) for i in range(4))  # type: ignore[return-value]


EDGE_STEP = 10  # grey levels between neighbours that count as an edge pixel
MIN_CONTINUITY = 0.25  # share of a line's pixels that must be edges, at least


def _peak(profile: np.ndarray, start: int, end: int, near_end: bool = False) -> Optional[int]:
    """The lasting edge in profile[start:end] nearest the face among the strong ones, or None.

    ``near_end`` when the face is past ``end`` (searching left of / above it). Taking the nearest
    strong edge, not the strongest, skips HUD bars and sub counters just outside the cam.
    """
    start, end = max(0, start), min(len(profile), end)
    if end - start < 4:
        return None
    region = profile[start:end]
    best = float(region.max())
    if best < max(MIN_CONTINUITY, float(np.median(region)) * 2.5):
        return None
    strong = np.flatnonzero(region >= 0.6 * best)
    return start + int(strong[-1] if near_end else strong[0])


def _sample_frames(path: Path, width: int, height: int, count: int = 8) -> list[np.ndarray]:
    import cv2

    cap = cv2.VideoCapture(str(path))
    frames = []
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or count
        for index in np.linspace(0, max(0, total - 1), count).astype(int):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok, frame = cap.read()
            if ok and frame.shape[:2] == (height, width):
                frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32))
    finally:
        cap.release()
    return frames


def find_cam_rect(path: Path, width: int, height: int, face: Box) -> Optional[Tuple[int, int, int, int]]:
    """The whole webcam rectangle around ``face``: edges that stay put while the gameplay moves.

    A side with no edge but reaching the frame border is taken as the border (corner cams).
    None when no rectangle stands out (a borderless cut-out cam): then the face crop is used.
    """
    frames = _sample_frames(path, width, height)
    if len(frames) < 4:
        return None
    fx, fy, fw, fh = face
    # Per column (row): the share of its pixels that are edges, median over time. The cam's border is
    # a straight line that lasts; shoulders, hair and the moving gameplay are not.
    rows = slice(max(0, int(fy - fh * 0.5)), min(height, int(fy + fh * 2)))
    vertical = np.median([(np.abs(np.diff(f[rows], axis=1)) > EDGE_STEP).mean(axis=0) for f in frames], axis=0)
    span = 5 * fw

    def side(start: float, end: float, border: int, near_end: bool) -> Optional[int]:
        found = _peak(vertical, int(start), int(end), near_end)
        if found is None and (start <= 0 or end >= width):
            return border
        return found

    left = side(fx - span, fx - 0.2 * fw, 0, True)
    right = side(fx + fw * 1.2, fx + fw + span, width - 1, False)
    if left is None or right is None:
        return None
    cols = slice(left + 4, max(left + 8, right - 3))
    horizontal = np.median([(np.abs(np.diff(f[:, cols], axis=0)) > EDGE_STEP).mean(axis=1) for f in frames], axis=0)
    top = _peak(horizontal, int(fy - 4 * fh), int(fy - 0.2 * fh), near_end=True)
    if top is None and fy - 4 * fh <= 0:
        top = 0
    bottom = _peak(horizontal, int(fy + fh * 1.2), int(fy + fh + 5 * fh))
    if bottom is None and fy + fh + 5 * fh >= height:
        bottom = height - 1
    if top is None or bottom is None:
        return None
    x, y = left, top
    w, h = right - left + 1, bottom - top + 1
    if not (0.12 * width <= w <= 0.6 * width and 0.12 * height <= h <= 0.75 * height and 0.8 <= w / h <= 2.6):
        return None
    return round_to_even(x), round_to_even(y), round_to_even(w), round_to_even(h)


def cam_crop(width: int, height: int, face: Box) -> Tuple[int, int, int, int]:
    """Head-and-shoulders crop around the webcam face, in the top area's 3:2 shape."""
    fx, fy, fw, fh = face
    ch = min(height, fh * 3.0)
    cw = ch * 1080 / TOP_H
    if cw > width:
        cw, ch = width, width * TOP_H / 1080
    w, h = round_to_even(int(cw)), round_to_even(int(ch))
    x = clamp_even(int(fx + fw / 2 - w / 2), 0, width - w)
    y = clamp_even(int(fy + fh / 2 - h * 0.4), 0, height - h)
    return x, y, w, h


def game_crop(width: int, height: int, face: Optional[Box]) -> Tuple[int, int, int, int]:
    """Full-height gameplay crop for the bottom area, on the side away from the webcam."""
    w = round_to_even(min(width, int(height * 1080 / GAME_H)))
    if face is None:
        x = round_to_even((width - w) // 2)
    elif face[0] + face[2] / 2 < width / 2:
        x = width - w
    else:
        x = 0
    return x, 0, w, height


# Archivo Black advance widths (em fractions), measured from backend/fonts/ArchivoBlack-Regular.ttf.
_GLYPH_W = {
    **dict.fromkeys("ABCDKRVXY", 0.778), **dict.fromkeys("EPST", 0.722), "F": 0.667, "J": 0.667, "L": 0.667,
    "Z": 0.722, **dict.fromkeys("GHKNOQU", 0.833), "I": 0.389, "M": 0.944, "W": 1.0,
    **dict.fromkeys("0123456789$", 0.667), " ": 0.333, **dict.fromkeys("!.,:;-", 0.333), "?": 0.611,
    "'": 0.278, "\u2019": 0.278, "/": 0.278, '"': 0.5, "\u201c": 0.5, "\u201d": 0.5, "&": 0.889, "%": 1.0,
    "#": 0.66, "+": 0.66, "=": 0.66, "@": 0.74, "*": 0.556, **dict.fromkeys("()[]", 0.389), "\u2026": 1.0,
}
TITLE_MAX_W = 1080 - 2 * 60  # 60 px margin each side
MIN_TITLE_PX = 30


def _border(size: int) -> int:
    return max(3, size // 12)


def title_width(line: str, size: int) -> float:
    """Rendered width in px of an uppercase line at ``size``, border included."""
    return sum(_GLYPH_W.get(c, 0.8) for c in line) * size + 2 * _border(size)


def _wrap(words: list[str], size: int) -> list[str]:
    lines = [""]
    for word in words:
        candidate = f"{lines[-1]} {word}".strip()
        if title_width(candidate, size) <= TITLE_MAX_W or not lines[-1]:
            lines[-1] = candidate
        else:
            lines.append(word)
    return lines


def title_lines(title: str) -> Tuple[list[str], int]:
    """At most two uppercase lines that fit between the margins, and their font size."""
    clean = "".join(c for c in (title or "").upper() if c in _GLYPH_W or c.isalnum())  # emoji would draw as boxes
    words = clean.split()
    if not words:
        return [""], MAX_TITLE_PX
    for size in range(MAX_TITLE_PX, MIN_TITLE_PX - 1, -2):
        lines = _wrap(words, size)
        if len(lines) <= 2 and all(title_width(line, size) <= TITLE_MAX_W for line in lines):
            return lines, size
    # Too long even at the smallest size: keep two lines and end the second with an ellipsis.
    size = MIN_TITLE_PX
    lines = _wrap(words, size)[:2]
    lines = [line if title_width(line, size) <= TITLE_MAX_W else _cut(line, size) for line in lines]
    last = lines[-1]
    while last and title_width(last + "\u2026", size) > TITLE_MAX_W:
        last = last[:-1]
    lines[-1] = last.rstrip() + "\u2026"
    return lines, size


def _cut(line: str, size: int) -> str:
    while line and title_width(line, size) > TITLE_MAX_W:
        line = line[:-1]
    return line


def _drawtext_value(text: str) -> str:
    """Text safe for drawtext inside -filter_complex (parsed twice: graph, then filter options)."""
    text = text.replace("\\", " ").replace("'", "\u2019")  # typographic apostrophe: nothing to escape
    for char in (":", ",", ";", "[", "]", "="):
        text = text.replace(char, "\\\\\\" + char)  # three backslashes survive both parses as one escape
    return text


def build_split_filter(width: int, height: int, face: Optional[Box], title: str,
                       cam_rect: Optional[Tuple[int, int, int, int]] = None,
                       game_rect: Optional[Tuple[int, int, int, int]] = None) -> str:
    """filter_complex from [0:v] to [vout]. With ``cam_rect`` the whole webcam is shown, fitted.

    With ``game_rect`` (the owner's game box) the gameplay comes from that region, and so does the
    blurred top when there is no webcam.
    """
    gx, gy, gw, gh = game_rect or game_crop(width, height, tuple(map(float, cam_rect)) if cam_rect else face)
    graph = ["[0:v]split=2[top_src][game_src]"]
    if cam_rect is not None:
        x, y, w, h = cam_rect
        graph.append(f"[top_src]crop={w}:{h}:{x}:{y},split=2[cam_bg][cam_fg]")
        graph.append(f"[cam_bg]scale=1080:{TOP_H}:force_original_aspect_ratio=increase,crop=1080:{TOP_H},boxblur=20:2[cam_fill]")
        graph.append(f"[cam_fg]scale=1080:{TOP_H}:force_original_aspect_ratio=decrease:flags=lanczos[cam_fit]")
        graph.append("[cam_fill][cam_fit]overlay=(W-w)/2:(H-h)/2,setsar=1[top]")
    elif face is not None:
        x, y, w, h = cam_crop(width, height, face)
        graph.append(f"[top_src]crop={w}:{h}:{x}:{y},scale=1080:{TOP_H}:flags=lanczos,setsar=1[top]")
    else:
        graph.append(
            f"[top_src]crop={gw}:{gh}:{gx}:{gy},scale=1080:{TOP_H}:force_original_aspect_ratio=increase,"
            f"crop=1080:{TOP_H},boxblur=20:2,setsar=1[top]"
        )
    # Owner: the whole game screen in 16:9, never cropped, centred on a blurred copy of itself.
    game_src = f"[game_src]crop={game_rect[2]}:{game_rect[3]}:{game_rect[0]}:{game_rect[1]}," if game_rect else "[game_src]"
    graph.append(f"{game_src}split=2[game_bg][game_fg]")
    graph.append(f"[game_bg]scale=1080:{GAME_H}:force_original_aspect_ratio=increase,crop=1080:{GAME_H},boxblur=20:2[game_fill]")
    graph.append(f"[game_fg]scale=1080:{GAME_H}:force_original_aspect_ratio=decrease:flags=lanczos[game_fit]")
    graph.append("[game_fill][game_fit]overlay=(W-w)/2:(H-h)/2,setsar=1[game]")
    lines, size = title_lines(title)
    line_h = int(size * 1.15)
    first_y = TOP_H + (BAR_H - line_h * len(lines)) // 2
    draws = "".join(
        f",drawtext=fontfile={TITLE_FONT}:expansion=none:text={_drawtext_value(line)}:fontcolor={WHITE}"
        f":borderw={_border(size)}:bordercolor={BLACK}"
        f":fontsize={size}:x=(w-text_w)/2:y={first_y + i * line_h}"
        for i, line in enumerate(lines) if line
    )
    graph.append(f"[top]pad=1080:{TOP_H + BAR_H}:0:0:color={YELLOW}{draws}[head]")
    graph.append("[head][game]vstack=inputs=2[vout]")
    return ";".join(graph)


def _framing(input_path: Path, width: int, height: int, channel_id: str):
    """The live channel's marked boxes, or None. Never breaks the render."""
    from . import framing

    try:
        framing.save_reference_frame(input_path, channel_id)
    except Exception as exc:
        logger.warning("Saving the framing frame failed (%s)", exc)
    try:
        return framing.channel_framing(channel_id, width, height)
    except Exception as exc:
        logger.warning("Reading the channel's framing failed (%s); using the detector", exc)
        return None


def plan_streamer_split(input_path: Path, width: int, height: int, title: str,
                        channel_id: Optional[str] = None) -> Tuple[str, str, float, Optional[Box]]:
    """(filter, "complex", caption_y, webcam face) for a horizontal clip, like ``build_vertical_filter_plan``.

    With ``channel_id`` (a live clip), the boxes the owner marked for that channel win over the detector.
    """
    from .reframing import analyze_vertical_clip

    # 960 px: a corner webcam face is too small to find reliably in the usual 480 px pass.
    samples = analyze_vertical_clip(input_path, proc_width=960, analyze_context=False)[2]
    face = find_streamer_cam(width, height, samples)
    marked = _framing(input_path, width, height, channel_id) if channel_id else None
    if marked is not None:
        cam_rect, game_rect = marked
        # the face only counts (for the per-voice captions) inside the marked webcam
        if face is not None and (cam_rect is None or not _inside(face, cam_rect)):
            face = None
        logger.info("Streamer split: channel framing (webcam %s, game %s)", cam_rect, game_rect)
        return build_split_filter(width, height, face, title, cam_rect, game_rect), "complex", CAPTION_Y, face
    cam_rect = find_cam_rect(input_path, width, height, face) if face is not None else None
    return build_split_filter(width, height, face, title, cam_rect), "complex", CAPTION_Y, face


def _inside(face: Box, rect: Tuple[int, int, int, int]) -> bool:
    cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
    return rect[0] <= cx <= rect[0] + rect[2] and rect[1] <= cy <= rect[1] + rect[3]
