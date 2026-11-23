"""Cat Clips 3.0 layout (fork): webcam on top, the game cut to the phone's width, a title badge between.

1080x1920 output, measured from the owner's reference: the webcam fills the top 700 px and the game the
bottom 1220 px, cut from the middle of the source without the bottom band where GTA draws its own
subtitles. The title sits on a slanted badge (Pricedown over a palm sunset, a doodled cat at its corner)
across the seam: it balloons in at the start and is sucked away after 3 s. The captions (streamer words, NPC lines) are
drawn later. Spec: docs/superpowers/specs/2026-11-23-cat-clips-3-design.md.
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
TOP_H, GAME_H = 700, 1220
GAME_Y = TOP_H
GAME_SHARE = 0.85  # the bottom 15% is where GTA draws its subtitles; it would show cut in half
BADGE_H, BADGE_MIN_W, BADGE_MAX_W = 170, 560, 980
BADGE_SKEW = 34  # px the top edge leans right
BADGE_TILT = -0.04  # radians: the right end a little higher
BADGE_PAD = 36
BADGE_Y = GAME_Y - BADGE_H // 2
TITLE_BOTTOM = BADGE_Y + BADGE_H
TITLE_SECONDS = 3.0  # then it's sucked away: shrinks to nothing while rising a little
TITLE_OUT = TITLE_SECONDS + 0.25
TITLE_FPS = 30
TITLE_FRAMES = int(TITLE_OUT * TITLE_FPS) + 2  # the still, repeated as long as it shows
# Scale over time, like a balloon: in from 10% past full size in 0.12 s, settles by 0.22 s, out in 0.25 s
POP = ("if(lt(t\\,0.12)\\,0.1+8.5*t\\,if(lt(t\\,0.22)\\,1.12-1.2*(t-0.12)\\,"
       f"if(lt(t\\,{TITLE_SECONDS:g})\\,1\\,max(0.02\\,1-3.9*(t-{TITLE_SECONDS:g})))))")
SUCK_RISE = f"if(gte(t\\,{TITLE_SECONDS:g})\\,400*(t-{TITLE_SECONDS:g})\\,0)"
TITLE_W = BADGE_MAX_W - BADGE_SKEW - 2 * BADGE_PAD
CAPTION_Y = (TITLE_BOTTOM + 210) / 1920  # the 2-line block (centred here) clears the title badge
BAR_BG_AT = 0.55  # owner picked the middle of the palm picture
# Local only: Pricedown's licence forbids sharing the file, and the picture is someone else's.
TITLE_FONT_V3 = Path(os.getenv("CATCLIPS3_TITLE_FONT", "/app/uploads/kit-assets/pricedown.otf"))
TITLE_BG_V3 = Path(os.getenv("CATCLIPS3_TITLE_BG", "/app/uploads/kit-assets/vice-city-palms.webp"))
DOODLES = Path(os.getenv("CATCLIPS3_DOODLES", "/app/assets/catclips3"))  # cat.png, dots.png (draw.py)
ZWNJ = "\u200c"  # between letters: stops Pricedown's ligatures (an extra stroke under "NT", "SS"...)

# Pricedown's advance widths and cap height, in ems, read from the font file (the file itself stays local)
PRICEDOWN_W = {
    **dict.fromkeys("ABCDGHJKNOPQRSUXZ02356890$", 0.516), "E": 0.403, "F": 0.43, "I": 0.26, "L": 0.384,
    "M": 0.777, "T": 0.549, "V": 0.528, "W": 0.837, "Y": 0.501, "1": 0.26, "4": 0.545, "7": 0.494, " ": 0.223,
    "!": 0.274, ".": 0.26, ",": 0.26, ":": 0.29, ";": 0.29, "-": 0.283, "?": 0.5, "\u2019": 0.241, "'": 0.241,
    "/": 0.423, '"': 0.494, "&": 0.537, "%": 0.682, "#": 0.615, "+": 0.484, "=": 0.484, "@": 0.499, "*": 0.451,
    **dict.fromkeys("()[]", 0.438), "\u2026": 1.3,
}
PRICEDOWN_CAP = 0.639
LINE_GAP = 0.25  # between two lines of capitals, in ems


def _overlap(a: Rect, b: Rect) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def game_crop(width: int, height: int, game_rect: Optional[Rect] = None, cam_rect: Optional[Rect] = None) -> Rect:
    """Cut of the game box with the output's game shape, centred, moved (or made shorter) so the webcam
    stays out of it."""
    gx, gy, gw, gh = game_rect or (0, 0, width, height)
    h = round_to_even(int(min(gh * GAME_SHARE, gw * GAME_H / 1080)))
    w = round_to_even(int(h * 1080 / GAME_H))
    x = clamp_even(gx + (gw - w) // 2, gx, gx + gw - w)
    if cam_rect is None or not _overlap((x, gy, w, h), cam_rect):
        return x, gy, w, h
    cx, cy, cw, _ = cam_rect
    right = cx + cw + (cx + cw) % 2  # even, never back into the cam
    shifts = [o for o in (round_to_even(cx - w), right) if gx <= o <= gx + gw - w]
    if shifts:
        return min(shifts, key=lambda o: abs(o - x)), gy, w, h
    if cy - gy >= gh * 0.5:  # cam right under the middle: keep the part above it
        h = round_to_even(cy - gy)
        w = round_to_even(int(h * 1080 / GAME_H))
        return clamp_even(gx + (gw - w) // 2, gx, gx + gw - w), gy, w, h
    return x, gy, w, h  # nowhere to dodge it: the cam shows twice, the game stays whole


def pricedown_width(line: str, size: int) -> float:
    """Rendered width in px of an uppercase line in Pricedown at ``size``, border included."""
    return sum(PRICEDOWN_W.get(c, 0.55) for c in line) * size + 2 * _border(size)


def _largest(lines: list[str], most: int) -> int:
    size = most
    while size > 30 and max(pricedown_width(line, size) for line in lines) > TITLE_W:
        size -= 2
    return size


def fit_title(title: str) -> tuple[list[str], int]:
    """One or two Pricedown lines and the biggest size that fits the badge (two lines split evenly)."""
    words = "".join(c for c in (title or "").upper() if c in PRICEDOWN_W or c.isalnum()).split()
    if not words:
        return [""], 60
    room = BADGE_H - 30
    options = [([" ".join(words)], _largest([" ".join(words)], min(120, int(room / PRICEDOWN_CAP))))]
    if len(words) > 1:
        two = min(([" ".join(words[:i]), " ".join(words[i:])] for i in range(1, len(words))),
                  key=lambda pair: max(pricedown_width(line, 100) for line in pair))
        options.append((two, _largest(two, int(room / (2 * PRICEDOWN_CAP + LINE_GAP)))))
    lines, size = max(options, key=lambda option: option[1])
    if max(pricedown_width(line, size) for line in lines) > TITLE_W:  # too long even at the smallest size
        lines = [line if pricedown_width(line, size) <= TITLE_W else _cut(line, size) for line in lines]
    return lines, size


def _cut(line: str, size: int) -> str:
    while line and pricedown_width(line + "\u2026", size) > TITLE_W:
        line = line[:-1]
    return line.rstrip() + "\u2026"


def badge_width(lines: list[str], size: int) -> int:
    widest = max(pricedown_width(line, size) for line in lines)
    return round_to_even(min(BADGE_MAX_W, max(BADGE_MIN_W, widest + BADGE_SKEW + 2 * BADGE_PAD)))


def _title_draws(lines: list[str], size: int, font: Path, join: bool) -> str:
    """drawtext for each line, the capitals centred on the badge (baseline placed with ``ascent``)."""
    block = len(lines) * PRICEDOWN_CAP * size + (len(lines) - 1) * LINE_GAP * size
    top = (BADGE_H - block) / 2
    return "".join(
        f",drawtext=fontfile={font}:expansion=none:text={_drawtext_value(ZWNJ.join(line) if join else line)}"
        f":fontcolor={WHITE}:borderw={_border(size)}:bordercolor={BLACK}:fontsize={size}:x=(w-text_w)/2"
        f":y={round(top + PRICEDOWN_CAP * size + i * (PRICEDOWN_CAP + LINE_GAP) * size)}-ascent"
        for i, line in enumerate(lines) if line
    )


def title_badge(title: str, font: Path = TITLE_FONT_V3, background: Path = TITLE_BG_V3,
                doodles: Path = DOODLES) -> str:
    """From ``[stack]`` (1080x1920) to ``[vout]``: the slanted title badge across the seam. It balloons in at
    the start and is sucked away after 3 s.

    Without the Pricedown file or the palm picture, the v2 look (Archivo Black on yellow).
    """
    # a path ffmpeg's filter parser would misread can't be passed safely: same as a missing file
    readable = not any(c in f"{font}{background}{doodles}" for c in "'\\:,;[]=")
    themed = readable and font.is_file() and background.is_file()
    if themed:
        lines, size = fit_title(title)
        width = badge_width(lines, size)
        fill = (f"movie={background},scale={width}:-2,crop={width}:{BADGE_H}:0:(ih-{BADGE_H})*{BAR_BG_AT},"
                f"format=rgba{_title_draws(lines, size, font, True)}")
    else:
        logger.warning("Cat Clips 3.0 title assets missing (%s, %s); using the v2 look", font, background)
        lines, size = title_lines(title)  # Archivo Black's widths, sized for a 960 px line
        size = min(size, int((BADGE_H - 30) / (len(lines) * 1.2)))
        width = BADGE_MAX_W
        fill = f"color=c={YELLOW}:s={width}x{BADGE_H}:d=1,format=rgba{_title_draws(lines, size, TITLE_FONT, False)}"
    lean = f"{BADGE_SKEW}*(1-Y/{BADGE_H})"
    graph = [
        f"{fill},geq=r='r(X\\,Y)':g='g(X\\,Y)':b='b(X\\,Y)'"
        f":a='255*between(X\\,{lean}\\,{width - BADGE_SKEW}+{lean})',"
        f"rotate={BADGE_TILT}:c=none:ow=rotw({BADGE_TILT}):oh=roth({BADGE_TILT}),split=2[badge_shadow][badge_still]",
        "[badge_shadow]lutrgb=r=0:g=0:b=0[shadow_still]",
    ]
    # every layer pops with the same scale around the badge's centre, keeping its offset from it
    layers = [("shadow_still", 10, 12), ("badge_still", 0, 0)]
    cat, dots = doodles / "cat.png", doodles / "dots.png"
    if readable and cat.is_file() and dots.is_file():
        graph += [f"movie={cat}[cat_still]", f"movie={dots}[dots_still]"]
        layers += [("cat_still", -width // 2 - 5, -90), ("dots_still", width // 2 + 34, -111)]
    base = "stack"
    for i, (name, dx, dy) in enumerate(layers):
        out = "vout" if i == len(layers) - 1 else f"title_{i}"
        graph.append(
            f"[{name}]loop=loop={TITLE_FRAMES}:size=1,setpts=N/{TITLE_FPS}/TB,"
            f"scale=w='max(2\\,trunc(iw*{POP}/2)*2)':h='max(2\\,trunc(ih*{POP}/2)*2)':eval=frame[{name}_pop]"
        )
        graph.append(
            f"[{base}][{name}_pop]overlay=x='540+{dx}*{POP}-w/2':y='{GAME_Y}+{dy}*{POP}-h/2-{SUCK_RISE}'"
            f":eof_action=pass:enable='lt(t\\,{TITLE_OUT:g})'[{out}]"
        )
        base = out
    return ";".join(graph)


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
    graph.append("[top][game]vstack=inputs=2[stack]")
    graph.append(title_badge(title, font, background))
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
