"""Cat Clips v2 layout (fork): streamer on top, title on a yellow bar, gameplay below.

1080x1920 output: the webcam crop fills the top 720 px, the clip title sits on a
200 px yellow bar, and the gameplay fills the bottom 1000 px with the captions at
its top. The webcam is found as a face that stays put through the clip, in any
part of the frame. Without one, the top shows the gameplay blurred.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence, Tuple

import numpy as np

from ..font_registry import FONTS_DIR
from .ffmpeg import clamp_even, round_to_even

Box = Tuple[float, float, float, float]
TOP_H, BAR_H, GAME_H = 720, 200, 1000
YELLOW, BLACK = "0xFEC803", "0x111111"
TITLE_FONT = FONTS_DIR / "ArchivoBlack-Regular.ttf"
MAX_TITLE_PX, MAX_LINE_CHARS = 64, 26
# Captions start at the top of the gameplay, just under the bar.
CAPTION_Y = (TOP_H + BAR_H + 70) / 1920


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


def title_lines(title: str, max_chars: int = MAX_LINE_CHARS) -> Tuple[list[str], int]:
    """At most two uppercase lines and the font size that fits them in the bar."""
    words = " ".join((title or "").split()).upper().split(" ")
    lines = [""]
    for word in words:
        candidate = f"{lines[-1]} {word}".strip()
        if len(candidate) <= max_chars or not lines[-1]:
            lines[-1] = candidate
        elif len(lines) < 2:
            lines.append(word)
        else:
            lines[-1] = candidate  # the last line takes the rest; the size shrinks to fit
    longest = max(len(line) for line in lines) or 1
    size = min(MAX_TITLE_PX, int(1000 / (longest * 0.66)))
    return lines, max(28, size)


def _drawtext_value(text: str) -> str:
    """Text safe for drawtext inside -filter_complex (parsed twice: graph, then filter options)."""
    text = text.replace("\\", " ").replace("'", "\u2019")  # typographic apostrophe: nothing to escape
    for char in (":", ",", ";", "[", "]", "="):
        text = text.replace(char, "\\\\\\" + char)  # three backslashes survive both parses as one escape
    return text


def build_split_filter(width: int, height: int, face: Optional[Box], title: str) -> str:
    """filter_complex from [0:v] to [vout]."""
    gx, gy, gw, gh = game_crop(width, height, face)
    graph = ["[0:v]split=2[top_src][game_src]"]
    if face is not None:
        x, y, w, h = cam_crop(width, height, face)
        graph.append(f"[top_src]crop={w}:{h}:{x}:{y},scale=1080:{TOP_H}:flags=lanczos,setsar=1[top]")
    else:
        graph.append(
            f"[top_src]crop={gw}:{gh}:{gx}:{gy},scale=1080:{TOP_H}:force_original_aspect_ratio=increase,"
            f"crop=1080:{TOP_H},boxblur=20:2,setsar=1[top]"
        )
    graph.append(f"[game_src]crop={gw}:{gh}:{gx}:{gy},scale=1080:{GAME_H}:flags=lanczos,setsar=1[game]")
    lines, size = title_lines(title)
    line_h = int(size * 1.15)
    first_y = TOP_H + (BAR_H - line_h * len(lines)) // 2
    draws = "".join(
        f",drawtext=fontfile={TITLE_FONT}:expansion=none:text={_drawtext_value(line)}:fontcolor={BLACK}"
        f":fontsize={size}:x=(w-text_w)/2:y={first_y + i * line_h}"
        for i, line in enumerate(lines) if line
    )
    graph.append(f"[top]pad=1080:{TOP_H + BAR_H}:0:0:color={YELLOW}{draws}[head]")
    graph.append("[head][game]vstack=inputs=2[vout]")
    return ";".join(graph)


def plan_streamer_split(input_path: Path, width: int, height: int, title: str) -> Tuple[str, str, float]:
    """(filter, "complex", caption_y) for a horizontal clip, like ``build_vertical_filter_plan``."""
    from .reframing import analyze_vertical_clip

    # 960 px: a corner webcam face is too small to find reliably in the usual 480 px pass.
    samples = analyze_vertical_clip(input_path, proc_width=960, analyze_context=False)[2]
    face = find_streamer_cam(width, height, samples)
    return build_split_filter(width, height, face, title), "complex", CAPTION_Y
