"""Sensitive blur (fork feature): decide per posting account, and the ffmpeg blur chain.

The worker stores, per rendered clip, where nudity and heavy gore are
(``live_clip_sensitive``). Before each post, the account's and the channel's
"Borrar nudez e gore" switches decide whether that clip must be blurred there.
When it must and the clip isn't safely checked, it is not posted (fail closed).
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

MAX_BLURRED_SHARE = 0.4  # more than this and the clip is too blurred to be worth posting
DURATION_SLACK = 0.2  # seconds; more drift means the file changed after the analysis


def _num(value: float) -> str:
    return f"{round(value, 3):g}"


def blurred_share(regions: list[dict[str, Any]], duration: float) -> float:
    """Share of the clip covered by at least one region (overlaps count once)."""
    spans = sorted((max(0.0, r["start"]), min(duration, r["end"])) for r in regions)
    covered, end = 0.0, 0.0
    for start, stop in spans:
        if stop <= end:
            continue
        covered += stop - max(start, end)
        end = stop
    return covered / duration if duration > 0 else 0.0


def decide(row: dict[str, Any], since: datetime, actual_duration: float) -> tuple[str, str | None, list]:
    """("post" | "blur" | "refuse", reason for a refusal, regions to blur) for one clip and account."""
    if not (row.get("target_blur") and row.get("channel_blur")):
        return "post", None, []
    regions = row.get("regions")
    if isinstance(regions, str):
        regions = json.loads(regions)
    if row.get("checked_duration") is None:
        created = row.get("clip_created")
        if created is not None and created < since:
            return "post", None, []  # made before the feature went live
        return "refuse", "clipe não analisado", []
    if row.get("check_error"):
        return "refuse", "análise falhou", []
    if abs(row["checked_duration"] - actual_duration) > DURATION_SLACK:
        return "refuse", "clipe alterado depois da análise", []
    if not regions:
        return "post", None, []
    if blurred_share(regions, actual_duration) > MAX_BLURRED_SHARE:
        return "refuse", "mais de 40% borrado", []
    return "blur", None, regions


def _even(value: float) -> int:
    return int(value) // 2 * 2


def blur_chain(regions: list[dict[str, Any]], width: int, height: int, label_in: str, label_out: str) -> list[str]:
    """Filter-graph parts that blur each region (fractions of the frame) during its seconds."""
    parts, current = [], label_in
    for i, region in enumerate(regions):
        x0 = _even(max(0.0, region["x"]) * width)
        y0 = _even(max(0.0, region["y"]) * height)
        x1 = min(width, math.ceil(min(1.0, region["x"] + region["w"]) * width))
        y1 = min(height, math.ceil(min(1.0, region["y"] + region["h"]) * height))
        w, h = _even(x1 - x0), _even(y1 - y0)
        if w < 4 or h < 4:
            continue
        nxt = label_out if i == len(regions) - 1 else f"[blur{i}]"
        parts += [
            f"{current}split[keep{i}][cut{i}]",
            f"[cut{i}]crop={w}:{h}:{x0}:{y0},boxblur=luma_radius=min(w\\,h)/6:luma_power=3"
            f":chroma_radius=min(cw\\,ch)/6:chroma_power=3[soft{i}]",
            f"[keep{i}][soft{i}]overlay={x0}:{y0}:enable='between(t,{_num(region['start'])},{_num(region['end'])})'{nxt}",
        ]
        current = nxt
    if current != label_out:  # every region was too small to draw
        parts.append(f"{current}null{label_out}")
    return parts
