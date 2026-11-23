"""Draws cat.png and dots.png (Cat Clips 3.0 title badge doodles) next to this file.

Run in the backend image: python backend/assets/catclips3/draw.py
"""
from pathlib import Path

import cv2
import numpy as np

OUT = Path(__file__).parent
CYAN = (255, 228, 43, 255)  # BGRA


def cat(size: int = 130, stroke: int = 9) -> np.ndarray:
    c = np.zeros((size, size, 4), np.uint8)
    s = size / 200

    def p(pts):
        return (np.array(pts, np.float32) * s).astype(np.int32)

    cv2.polylines(c, [p([(40, 112), (34, 34), (78, 70), (122, 70), (166, 34), (160, 112)])], False, CYAN, stroke,
                  cv2.LINE_AA)
    cv2.ellipse(c, (int(100 * s), int(112 * s)), (int(60 * s), int(52 * s)), 0, 0, 180, CYAN, stroke, cv2.LINE_AA)
    for x in (76, 124):
        cv2.circle(c, (int(x * s), int(112 * s)), int(9 * s), CYAN, -1, cv2.LINE_AA)
    cv2.fillPoly(c, [p([(92, 132), (108, 132), (100, 142)])], CYAN, cv2.LINE_AA)
    for y0, y1 in ((134, 126), (144, 146)):
        cv2.line(c, (int(56 * s), int(y0 * s)), (int(8 * s), int(y1 * s)), CYAN, max(3, stroke // 2), cv2.LINE_AA)
        cv2.line(c, (int(144 * s), int(y0 * s)), (int(192 * s), int(y1 * s)), CYAN, max(3, stroke // 2), cv2.LINE_AA)
    m = cv2.getRotationMatrix2D((size / 2, size / 2), 18, 1.0)  # tilted, like the reference's crown
    return cv2.warpAffine(c, m, (size, size), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))


def dots() -> np.ndarray:
    d = np.zeros((60, 60, 4), np.uint8)
    cv2.circle(d, (16, 16), 11, CYAN, -1, cv2.LINE_AA)
    cv2.circle(d, (44, 42), 8, CYAN, -1, cv2.LINE_AA)
    return d


if __name__ == "__main__":
    cv2.imwrite(str(OUT / "cat.png"), cat())
    cv2.imwrite(str(OUT / "dots.png"), dots())
