"""Where Cat Clips 3.0 draws an NPC's line (fork): above the NPC's head, in the 1080x1920 output.

Faces come from the layout's face samples (source pixels, the clip's timeline). While the NPC that talks
isn't on screen yet, the line waits at the edge on the side where it will show up (the clip is already
recorded, so we can look ahead); with no NPC face anywhere in the clip, it sits at a fixed spot.
Spec: docs/superpowers/specs/2026-11-23-cat-clips-3-design.md.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from .catclips3 import GAME_H, GAME_Y, TITLE_BOTTOM

Face = tuple[float, float, float, float]
Point = tuple[int, int, Face]
Track = list[tuple[float, list[Point]]]
Segment = tuple[float, float, int, int, int, int]  # t0, t1, x0, y0, x1, y1 (bottom-centre of the text)

EDGE_X = 70
Y_MIN = TITLE_BOTTOM + 100  # the text sits above this anchor: it never covers the title badge
Y_MAX = 1920 - 40
EDGE_Y = GAME_Y + round(0.45 * GAME_H)
FIXED = (540, GAME_Y + round(0.62 * GAME_H))
HEAD_GAP = 20


def _clamp(value: float, low: float, high: float) -> int:
    return round(min(max(value, low), high))


def head_track(samples: Sequence[tuple[float, Sequence[Face]]], cam_face: Optional[Face],
               crop: tuple[int, int, int, int]) -> Track:
    """Per sample, the output point just above each game face inside the crop (the webcam face left out)."""
    cx, cy, cw, ch = crop
    scale = 1080 / cw
    track: Track = []
    for t, faces in samples:
        points = []
        for face in faces:
            mx, my = face[0] + face[2] / 2, face[1] + face[3] / 2
            if cam_face is not None and abs(mx - (cam_face[0] + cam_face[2] / 2)) < max(cam_face[2], face[2]) \
                    and abs(my - (cam_face[1] + cam_face[3] / 2)) < max(cam_face[3], face[3]):
                continue
            if not (cx <= mx <= cx + cw and cy <= my <= cy + ch):
                continue
            x = _clamp((mx - cx) * scale, EDGE_X, 1080 - EDGE_X)
            y = _clamp(GAME_Y + (face[1] - cy) * scale - HEAD_GAP, Y_MIN, Y_MAX)
            points.append((x, y, face))
        track.append((float(t), points))
    return track


def _edge(x: float, y: int = EDGE_Y) -> tuple[int, int]:
    return (EDGE_X if x < 540 else 1080 - EDGE_X), y


def line_path(start: float, end: float, track: Track,
              speaker: Optional[Callable[[float, float, list[Face]], int]] = None) -> list[Segment]:
    """Where one NPC line sits from ``start`` to ``end``: segments the caption moves along."""
    seen = [(t, pts) for t, pts in track if start <= t <= end and pts]
    if not seen:
        later = next((pts for t, pts in track if t > end and pts), None)
        earlier = next((pts for t, pts in reversed(track) if t < start and pts), None)
        if later is None and earlier is None:
            return [(start, end, *FIXED, *FIXED)]
        x, _, _ = min(later or earlier, key=lambda p: abs(p[0] - 540))
        edge = _edge(x)
        return [(start, end, *edge, *edge)]
    first = seen[0][1]
    pick = min(range(len(first)), key=lambda i: abs(first[i][0] - 540))
    if speaker and len(first) > 1:
        try:
            pick = speaker(start, end, [p[2] for p in first])
        except Exception:  # can't tell whose mouth moves: the face nearest the centre
            pass
    current = first[pick][:2]
    keys = []
    for t, pts in seen:  # follow the same face: the nearest to where the line was
        current = min(pts, key=lambda p: (p[0] - current[0]) ** 2 + (p[1] - current[1]) ** 2)[:2]
        keys.append((t, current))
    segments: list[Segment] = []
    t0, p0 = keys[0]
    if t0 > start:
        before = next((pts for t, pts in reversed(track) if t < start), [])
        hold = p0 if before else _edge(p0[0], p0[1])  # already on screen, or still coming in from its side
        segments.append((start, t0, *hold, *hold))
    for (ta, pa), (tb, pb) in zip(keys, keys[1:]):
        segments.append((ta, tb, *pa, *pb))
    tl, pl = keys[-1]
    if tl < end:
        segments.append((tl, end, *pl, *pl))
    merged: list[Segment] = []
    for seg in segments:
        if merged and merged[-1][2:] == seg[2:] and seg[2:4] == seg[4:6]:
            merged[-1] = (merged[-1][0], seg[1], *seg[2:])
        else:
            merged.append(seg)
    return merged


def segments_between(path: Sequence[Segment], start: float, end: float) -> list[Segment]:
    """The part of ``path`` between ``start`` and ``end``, positions interpolated at the cuts."""
    out = []
    for t0, t1, x0, y0, x1, y1 in path:
        a, b = max(t0, start), min(t1, end)
        if b <= a:
            continue
        span = (t1 - t0) or 1.0

        def at(t: float) -> tuple[int, int]:
            f = (t - t0) / span
            return round(x0 + (x1 - x0) * f), round(y0 + (y1 - y0) * f)

        out.append((a, b, *at(a), *at(b)))
    return out


def annotate_npc_words(words: list[dict[str, Any]], track: Track,
                       speaker: Optional[Callable[[float, float, list[Face]], int]] = None,
                       gap: float = 1.0) -> list[dict[str, Any]]:
    """Copies of ``words`` where every "game" word carries its line's ``npc_path``.

    Lines split like caption lines (a pause over ``gap`` or a cut in the edit), so a caption line never
    outlives its path.
    """
    out = [dict(word) for word in words]
    i = 0
    while i < len(out):
        if out[i].get("role") != "game":
            i += 1
            continue
        j = i
        while j + 1 < len(out) and out[j + 1].get("role") == "game" and not out[j + 1].get("break_before") \
                and float(out[j + 1]["start"]) - float(out[j]["end"]) <= gap:
            j += 1
        path = line_path(float(out[i]["start"]), float(out[j]["end"]), track, speaker)
        for k in range(i, j + 1):
            out[k]["npc_path"] = path
        i = j + 1
    return out
