"""Where Cat Clips 3.0 draws an NPC's line (fork): above the NPC's head, in the 1080x1920 output.

Faces come from the layout's face samples (source pixels, the clip's timeline). While the NPC that talks
isn't on screen yet, the line waits at the edge on the side where it will show up (the clip is already
recorded, so we can look ahead); with no NPC face anywhere in the clip, it sits at a fixed spot.
Spec: docs/superpowers/specs/2026-11-23-cat-clips-3-design.md.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from .catclips3 import GAME_H, GAME_Y, STREAMER_Y, STREAMER_Y_TITLE, TITLE_BOTTOM, TITLE_OUT

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
# The protagonist (and the voice on their phone) talks from above the player, whom the 3.0 crop centres:
# the arrow's tip lands about over their head (GTA 6's chase camera keeps it near here on foot).
PLAYER_SPOT = (540, GAME_Y + round(0.36 * GAME_H))
PLAYER_BOX = (120, 320)  # how far (x, y) from the player's spot a face still counts as the player
SEAT_MOVE = 0.25  # seconds the streamer's caption takes to climb onto the seam once the title is gone


def streamer_path() -> list[Segment]:
    """Where the streamer's caption block sits (its centre): under the title, then on the seam."""
    up = TITLE_OUT + SEAT_MOVE
    return [(0.0, TITLE_OUT, 540, STREAMER_Y_TITLE, 540, STREAMER_Y_TITLE),
            (TITLE_OUT, up, 540, STREAMER_Y_TITLE, 540, STREAMER_Y),
            (up, 1e6, 540, STREAMER_Y, 540, STREAMER_Y)]


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
            if abs(x - PLAYER_SPOT[0]) <= PLAYER_BOX[0] and abs(y - PLAYER_SPOT[1]) <= PLAYER_BOX[1]:
                continue  # the protagonist the crop centres on: an NPC's line never points at them
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
    """Copies of ``words`` where every "game" word carries its line's ``npc_path`` and every streamer word
    the ``seat_path`` of the streamer's caption.

    Lines split like caption lines (a pause over ``gap``, a cut in the edit, another voice), so a caption
    line never outlives its path. The protagonist's and the phone's lines sit over the player; an NPC's
    over the NPC.
    """
    out = [dict(word) for word in words]
    seat = streamer_path()
    i = 0
    while i < len(out):
        if out[i].get("role") != "game":
            if out[i].get("role") == "streamer":
                out[i]["seat_path"] = seat
            i += 1
            continue
        j = i
        while j + 1 < len(out) and out[j + 1].get("role") == "game" and not out[j + 1].get("break_before") \
                and out[j + 1].get("voice") == out[i].get("voice") and out[j + 1].get("npc") == out[i].get("npc") \
                and float(out[j + 1]["start"]) - float(out[j]["end"]) <= gap:
            j += 1
        start, end = float(out[i]["start"]), float(out[j]["end"])
        if out[i].get("voice") in ("protagonist", "phone"):
            path = [(start, end, *PLAYER_SPOT, *PLAYER_SPOT)]
        else:
            path = line_path(start, end, track, speaker)
        for k in range(i, j + 1):
            out[k]["npc_path"] = path
        i = j + 1
    return out
