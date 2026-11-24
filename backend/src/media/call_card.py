"""Cat Clips 3.0: phone calls, read off GTA 6's call card (owner's idea).

While someone talks to the protagonist on the phone, GTA 6 shows a card at the bottom right: the
caller's avatar with a green phone badge, their name, and the call's timer ("Jason / 00:04"); then
"Call Ended". The card falls outside the 3.0 crop, so it's read on the source.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np

CARD_HZ = 1
# shortcut: the card's place assumes the game fills the frame (as the subtitle reader does); a stream
# layout with the game in a box would need the game rect here.
BADGE = (0.88, 0.99, 0.76, 0.86)  # rows, cols (share of the frame) around the avatar's green badge
TEXT = (0.88, 0.99, 0.805, 0.99)  # the name and the timer
MIN_GREEN = 120  # the badge is ~300 px at 1080p; nothing else green sits there in GTA's HUD
TIMER = re.compile(r"\d{1,2}\s*[:.]\s*\d{2}")


def _part(frame: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    h, w = frame.shape[:2]
    return frame[int(box[0] * h):int(box[1] * h), int(box[2] * w):int(box[3] * w)]


def badge_on(frame: np.ndarray) -> bool:
    b, g, r = (_part(frame, BADGE)[..., i].astype(int) for i in range(3))
    return int(((g > 140) & (g - r > 60) & (g - b > 40)).sum()) >= MIN_GREEN * frame.shape[0] / 1080


def caller(text: str) -> Optional[str]:
    """The caller's name when the card shows a call in progress (a running timer), else None."""
    if not TIMER.search(text) or re.search(r"ended|dialing|calling", text, re.I):
        return None
    for line in text.splitlines():
        name = re.findall(r"[A-Z][A-Za-z'\-]+", line)
        if name:
            return " ".join(name)
    return None


def card_text(frame: np.ndarray) -> str:
    import subprocess

    import cv2

    gray = cv2.cvtColor(_part(frame, TEXT), cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, ink = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)
    png = cv2.imencode(".png", ink)[1].tobytes()
    return subprocess.run(["tesseract", "stdin", "stdout", "--psm", "6", "-l", "eng"], input=png,
                          capture_output=True, timeout=20, check=True).stdout.decode(errors="ignore").strip()


def spans(times: list[float], names: list[Optional[str]], gap: float = 2.5) -> list[tuple[float, float, str]]:
    """(from, to, caller) of each call: the same caller read on samples less than ``gap`` apart."""
    out: list[list] = []
    for t, name in zip(times, names):
        if name is None:
            continue
        if out and out[-1][2] == name and t - out[-1][1] <= gap:
            out[-1][1] = t
        else:
            out.append([t, t, name])
    half = 0.5 / CARD_HZ
    return [(max(0.0, a - half), b + half, name) for a, b, name in out]


def calls(path: Path, start: float, end: float) -> list[tuple[float, float, str]]:
    """The phone calls on screen between ``start`` and ``end`` (clip seconds)."""
    from .voice_roles import _frames, _index

    times = [float(t) for t in np.arange(max(0.0, start), end, 1 / CARD_HZ)]
    seen = {index: caller(card_text(frame)) if badge_on(frame) else None for index, frame in _frames(path, times)}
    return spans(times, [seen.get(index) for index in _index(path, times)])
