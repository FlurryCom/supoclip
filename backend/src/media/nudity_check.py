"""Sensitive blur (fork feature): exposed nudity found by the NudeNet model.

Runs the NudeNet 320n detector (assets/models, see NOTICE.md) with onnxruntime
on sampled frames, keeps exposed classes above the score threshold, pads each
box and merges boxes that persist across neighbouring samples into blur regions.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np

MODEL = Path(__file__).resolve().parents[2] / "assets" / "models" / "nudenet-320n.onnx"
SIZE = 320
LABELS = (
    "FEMALE_GENITALIA_COVERED", "FACE_FEMALE", "BUTTOCKS_EXPOSED", "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED", "MALE_BREAST_EXPOSED", "ANUS_EXPOSED", "FEET_EXPOSED",
    "BELLY_COVERED", "FEET_COVERED", "ARMPITS_COVERED", "ARMPITS_EXPOSED", "FACE_MALE",
    "BELLY_EXPOSED", "MALE_GENITALIA_EXPOSED", "ANUS_COVERED", "FEMALE_BREAST_COVERED",
    "BUTTOCKS_COVERED",
)
EXPOSED = frozenset({
    "FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
    "BUTTOCKS_EXPOSED", "ANUS_EXPOSED",
})
MIN_SCORE = 0.45
PAD = 0.15


class NudityModel:
    _instance: "NudityModel | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        import onnxruntime

        self.session = onnxruntime.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    @classmethod
    def get(cls) -> "NudityModel":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def detect(self, frame_bgr: np.ndarray, only: frozenset[str] | None = None, min_score: float = 0.2):
        """(label, score, (x, y, w, h) in pixels) per detection after NMS; ``only`` limits the classes first."""
        h, w = frame_bgr.shape[:2]
        side = max(h, w)
        # Same colour handling as upstream NudeNet (its scores were tuned on this pipeline).
        image = cv2.cvtColor(frame_bgr, cv2.COLOR_RGBA2BGR)
        square = cv2.copyMakeBorder(image, 0, side - h, 0, side - w, cv2.BORDER_CONSTANT)
        blob = cv2.dnn.blobFromImage(square, 1 / 255.0, (SIZE, SIZE), (0, 0, 0), swapRB=True, crop=False)
        return decode(self.session.run(None, {self.input_name: blob})[0], side, w, h, only, min_score)


def decode(output: np.ndarray, side: int, width: int, height: int,
           only: frozenset[str] | None = None, min_score: float = 0.2) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """Model output (1, 4 + classes, anchors) -> detections after NMS.

    Without ``only`` this matches upstream NudeNet. With it, only those classes
    are scored and overlap-filtered, so a face or covered-body box can never
    suppress an exposed one (what matters for a safety filter).
    """
    rows = np.squeeze(output).T  # (anchors, 4 + classes)
    if rows.ndim == 1:
        rows = rows[None, :]
    class_ids = np.arange(len(LABELS)) if only is None else np.array([LABELS.index(label) for label in sorted(only)])
    class_scores = rows[:, 4 + class_ids]
    scores = class_scores.max(axis=1)
    classes = class_ids[class_scores.argmax(axis=1)]
    keep = scores >= max(0.2, min_score)
    rows, scores, classes = rows[keep], scores[keep], classes[keep]
    if not len(rows):
        return []
    scale = side / SIZE
    boxes = []
    for cx, cy, bw, bh in rows[:, :4]:
        x, y = max(0.0, (cx - bw / 2) * scale), max(0.0, (cy - bh / 2) * scale)
        boxes.append([int(x), int(y), int(min(bw * scale, width - x)), int(min(bh * scale, height - y))])
    picked = cv2.dnn.NMSBoxes(boxes, scores.tolist(), 0.25, 0.45)
    return [(LABELS[int(classes[i])], float(scores[i]), tuple(boxes[i])) for i in np.array(picked).flatten()]


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _union(a, b):
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    return (x0, y0, max(a[0] + a[2], b[0] + b[2]) - x0, max(a[1] + a[3], b[1] + b[3]) - y0)


def _clamp(box):
    x0, y0 = max(0.0, box[0]), max(0.0, box[1])
    x1, y1 = min(1.0, box[0] + box[2]), min(1.0, box[1] + box[3])
    return (x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0))


def merge_regions(samples: list[tuple[float, list[tuple[float, float, float, float]]]], step: float, gap: float = 1.0) -> list[dict[str, Any]]:
    """Boxes (fractions) per sampled second -> regions; a box joins an open region it overlaps within ``gap``."""
    open_regions: list[dict[str, Any]] = []
    done: list[dict[str, Any]] = []
    for second, boxes in sorted(samples):
        for region in [r for r in open_regions if second - r["last"] > gap]:
            open_regions.remove(region)
            done.append(region)
        for box in boxes:
            box = _clamp(box)
            match = next((r for r in open_regions if _overlaps(r["box"], box)), None)
            if match:
                match["box"], match["last"] = _union(match["box"], box), second
            else:
                open_regions.append({"start": second, "last": second, "box": box})
    done += open_regions
    return [
        # Start one sample early: the content may have appeared any time since the previous sample.
        {"kind": "nudity", "start": max(0.0, r["start"] - step), "end": r["last"] + step,
         "x": r["box"][0], "y": r["box"][1], "w": r["box"][2], "h": r["box"][3]}
        for r in sorted(done, key=lambda r: r["start"])
    ]


def find_nudity(frames: list[tuple[float, np.ndarray]], step: float = 0.5) -> list[dict[str, Any]]:
    """Exposed-nudity regions in frames sampled every ``step`` seconds."""
    model = NudityModel.get()
    samples = []
    for second, frame in frames:
        h, w = frame.shape[:2]
        boxes = []
        for label, score, (x, y, bw, bh) in model.detect(frame, only=EXPOSED, min_score=MIN_SCORE):
            if label in EXPOSED and score >= MIN_SCORE:
                px, py = bw * PAD, bh * PAD
                boxes.append(((x - px) / w, (y - py) / h, (bw + 2 * px) / w, (bh + 2 * py) / h))
        samples.append((second, boxes))
    return merge_regions(samples, step)
