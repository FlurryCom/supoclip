"""Cat Clips 3.0 layout (fork): webcam on top, Vice City title bar, the game cut to the phone's width.

1080x1920 output: the webcam fills the top 640 px, the title sits on a 200 px bar (Pricedown over a palm
sunset), and the game fills the bottom 1080 px as a square cut from the middle of the source, without the
bottom band where GTA draws its own subtitles. The captions (streamer words, NPC lines) are drawn later.
Spec: docs/superpowers/specs/2026-11-23-cat-clips-3-design.md.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from .ffmpeg import clamp_even, round_to_even
from .streamer_split import (
    BLACK, TITLE_FONT, WHITE, YELLOW, Box, _border, _drawtext_value, _framing, _inside, cam_crop,
    find_cam_rect, find_streamer_cam, title_lines,
)

logger = logging.getLogger(__name__)

Rect = Tuple[int, int, int, int]
TOP_H, BAR_H, GAME_H = 640, 200, 1080
GAME_Y = TOP_H + BAR_H
CAPTION_Y = (GAME_Y + 210) / 1920  # the 2-line block (centred here) clears the title bar
GAME_SHARE = 0.85  # the bottom 15% is where GTA draws its subtitles; it would show cut in half
BAR_BG_AT = 0.55  # owner picked the middle of the palm picture
# Local only: Pricedown's licence forbids sharing the file, and the picture is someone else's.
TITLE_FONT_V3 = Path(os.getenv("CATCLIPS3_TITLE_FONT", "/app/uploads/kit-assets/pricedown.otf"))
TITLE_BG_V3 = Path(os.getenv("CATCLIPS3_TITLE_BG", "/app/uploads/kit-assets/vice-city-palms.webp"))
ZWNJ = "‌"  # between letters: stops Pricedown's ligatures (an extra stroke under "NT", "SS"...)


def _overlap(a: Rect, b: Rect) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def game_crop(width: int, height: int, game_rect: Optional[Rect] = None, cam_rect: Optional[Rect] = None) -> Rect:
    """Square cut of the game box, centred, moved (or made shorter) so the webcam stays out of it."""
    gx, gy, gw, gh = game_rect or (0, 0, width, height)
    side = round_to_even(min(int(gh * GAME_SHARE), gw))
    x = clamp_even(gx + (gw - side) // 2, gx, gx + gw - side)
    if cam_rect is None or not _overlap((x, gy, side, side), cam_rect):
        return x, gy, side, side
    cx, cy, cw, _ = cam_rect
    right = cx + cw + (cx + cw) % 2  # even, never back into the cam
    shifts = [o for o in (round_to_even(cx - side), right) if gx <= o <= gx + gw - side]
    if shifts:
        return min(shifts, key=lambda o: abs(o - x)), gy, side, side
    if cy - gy >= gh * 0.5:  # cam right under the middle: keep the part above it
        side = round_to_even(min(cy - gy, gw))
        return clamp_even(gx + (gw - side) // 2, gx, gx + gw - side), gy, side, side
    return x, gy, side, side  # nowhere to dodge it: the cam shows twice, the game stays whole


def _title_draws(lines: list[str], size: int, font: Path, join: bool) -> str:
    line_h = int(size * 1.15)
    first_y = TOP_H + (BAR_H - line_h * len(lines)) // 2
    return "".join(
        f",drawtext=fontfile={font}:expansion=none:text={_drawtext_value(ZWNJ.join(line) if join else line)}"
        f":fontcolor={WHITE}:borderw={_border(size)}:bordercolor={BLACK}"
        f":fontsize={size}:x=(w-text_w)/2:y={first_y + i * line_h}"
        for i, line in enumerate(lines) if line
    )


def title_bar(title: str, font: Path = TITLE_FONT_V3, background: Path = TITLE_BG_V3) -> str:
    """From ``[top]`` (1080x640) to ``[head]`` (1080x840): the title bar under the webcam.

    Without the Pricedown file or the palm picture, the v2 bar (Archivo Black on yellow).
    """
    # shortcut: wraps with Archivo Black's widths, wider than Pricedown, so lines never overflow
    lines, size = title_lines(title)
    # a path ffmpeg's filter parser would misread can't be passed safely: same as a missing file
    readable = not any(c in f"{font}{background}" for c in "'\\:,;[]=")
    if not (readable and font.is_file() and background.is_file()):
        logger.warning("Cat Clips 3.0 title assets missing (%s, %s); using the v2 bar", font, background)
        return f"[top]pad=1080:{GAME_Y}:0:0:color={YELLOW}{_title_draws(lines, size, TITLE_FONT, False)}[head]"
    return (
        f"[top]pad=1080:{GAME_Y}:0:0:color={YELLOW}[headbg];"
        f"movie={background},scale=1080:-2,"
        f"crop=1080:{BAR_H}:0:(ih-{BAR_H})*{BAR_BG_AT},setsar=1[palms];"
        # a single still frame, repeated for the whole clip
        f"[headbg][palms]overlay=0:{TOP_H}:eof_action=repeat{_title_draws(lines, size, font, True)}[head]"
    )


def build_v3_filter(width: int, height: int, face: Optional[Box], title: str, cam_rect: Optional[Rect],
                    crop: Rect, font: Path = TITLE_FONT_V3, background: Path = TITLE_BG_V3) -> str:
    """filter_complex from [0:v] to [vout]."""
    graph = ["[0:v]split=2[top_src][game_src]"]
    if cam_rect is not None:
        x, y, w, h = cam_rect
        graph.append(f"[top_src]crop={w}:{h}:{x}:{y},split=2[cam_bg][cam_fg]")
        graph.append(f"[cam_bg]scale=1080:{TOP_H}:force_original_aspect_ratio=increase,crop=1080:{TOP_H},boxblur=20:2[cam_fill]")
        graph.append(f"[cam_fg]scale=1080:{TOP_H}:force_original_aspect_ratio=decrease:flags=lanczos[cam_fit]")
        graph.append("[cam_fill][cam_fit]overlay=(W-w)/2:(H-h)/2,setsar=1[top]")
    elif face is not None:
        x, y, w, h = cam_crop(width, height, face)
        graph.append(f"[top_src]crop={w}:{h}:{x}:{y},scale=1080:{TOP_H}:force_original_aspect_ratio=increase,"
                     f"crop=1080:{TOP_H},setsar=1[top]")
    else:  # no webcam: the game, blurred
        x, y, w, h = crop
        graph.append(f"[top_src]crop={w}:{h}:{x}:{y},scale=1080:{TOP_H}:force_original_aspect_ratio=increase,"
                     f"crop=1080:{TOP_H},boxblur=20:2,setsar=1[top]")
    x, y, w, h = crop
    graph.append(f"[game_src]crop={w}:{h}:{x}:{y},scale=1080:{GAME_H}:flags=lanczos,setsar=1[game]")
    graph.append(title_bar(title, font, background))
    graph.append("[head][game]vstack=inputs=2[vout]")
    return ";".join(graph)


def plan_catclips3(input_path: Path, width: int, height: int, title: str, channel_id: Optional[str] = None,
                   recording_id: Optional[str] = None):
    """(filter, "complex", caption_y, webcam face, game crop, face samples) for a horizontal clip."""
    from .reframing import analyze_vertical_clip

    samples = analyze_vertical_clip(input_path, proc_width=960, analyze_context=False)[2]
    face = find_streamer_cam(width, height, samples)
    decision = _framing(input_path, width, height, channel_id, recording_id, samples, face) if channel_id else None
    if decision is not None and decision.kind != "detector":
        cam_rect, game_rect = decision.cam, decision.game
        if face is not None and (cam_rect is None or not _inside(face, cam_rect)):
            face = None
    else:
        game_rect = decision.game if decision is not None else None
        cam_rect = find_cam_rect(input_path, width, height, face) if face is not None else None
    # only a real webcam box is dodged: a lone face may be a game character (cutscene close-up)
    crop = game_crop(width, height, game_rect, cam_rect)
    logger.info("Cat Clips 3.0: webcam %s, game crop %s", cam_rect, crop)
    graph = build_v3_filter(width, height, face, title, cam_rect, crop)
    return graph, "complex", CAPTION_Y, face, crop, samples
