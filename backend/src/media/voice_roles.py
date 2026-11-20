"""Who is talking in a live clip (fork, Cat Clips v2): the streamer, a friend, or the game.

Speech is cut into stretches between pauses; a small speaker-embedding model (CAM++ from
3D-Speaker, ONNX, CPU) turns each stretch into a voice vector, and stretches with the same
voice are grouped. The streamer's group is the one whose speech lines up with the mouth
moving in the webcam (or, without a cam, the one that talks most). Other voices are the game
when the game shows its own subtitles while they talk, a friend when they talk back and forth
with the streamer, and unsure otherwise (owner: unsure lines are left out).
Any failure keeps every word as the streamer, exactly like captions were before.
"""
from __future__ import annotations

import logging
import os
import urllib.request
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

RATE = 16000
MODEL_PATH = Path(os.getenv("VOICE_MODEL_PATH", "/app/uploads/models/voice/campplus.onnx"))
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
             "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx")
MIN_EMBED_SECONDS = 0.8
_session = None


def speech_segments(words: list[dict[str, Any]], gap: float = 0.6) -> list[tuple[float, float, list[int]]]:
    """Stretches of speech (start s, end s, word indexes) split at pauses longer than ``gap``."""
    segments: list[tuple[float, float, list[int]]] = []
    for index, word in enumerate(words):
        start, end = word["start"] / 1000.0, word["end"] / 1000.0
        if segments and start - segments[-1][1] <= gap:
            s, _, idx = segments[-1]
            segments[-1] = (s, max(end, segments[-1][1]), idx + [index])
        else:
            segments.append((start, end, [index]))
    return segments


def _mel_banks(bins: int = 80, fft: int = 512, low: float = 20.0, high: float = RATE / 2) -> np.ndarray:
    def mel(f):
        return 1127.0 * np.log(1.0 + np.asarray(f) / 700.0)

    points = np.linspace(mel(low), mel(high), bins + 2)
    freqs = mel(np.arange(fft // 2 + 1) * RATE / fft)
    banks = np.zeros((bins, fft // 2 + 1), dtype=np.float32)
    for b in range(bins):
        left, centre, right = points[b], points[b + 1], points[b + 2]
        up = (freqs - left) / (centre - left)
        down = (right - freqs) / (right - centre)
        banks[b] = np.clip(np.minimum(up, down), 0, None)
    return banks


_BANKS = _mel_banks()


def fbank(samples: np.ndarray, rate: int = RATE) -> np.ndarray:
    """Kaldi-style 80-bin log-mel features (25 ms window, 10 ms hop), mean removed per clip."""
    frame, hop, fft = int(0.025 * rate), int(0.010 * rate), 512
    if len(samples) < frame:
        return np.zeros((0, 80), dtype=np.float32)
    count = 1 + (len(samples) - frame) // hop
    index = np.arange(frame)[None, :] + hop * np.arange(count)[:, None]
    frames = samples[index].astype(np.float32) * 32768.0
    frames -= frames.mean(axis=1, keepdims=True)
    frames[:, 1:] -= 0.97 * frames[:, :-1]
    frames[:, 0] *= 1 - 0.97
    frames *= (np.hanning(frame) ** 0.85).astype(np.float32)  # Povey window
    power = np.abs(np.fft.rfft(frames, n=fft)) ** 2
    feats = np.log(np.maximum(power @ _BANKS.T, 1e-10)).astype(np.float32)
    return feats - feats.mean(axis=0, keepdims=True)


def ensure_model() -> Optional[Path]:
    if not MODEL_PATH.exists():
        try:
            MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            partial = MODEL_PATH.with_suffix(".part")
            urllib.request.urlretrieve(MODEL_URL, partial)
            partial.rename(MODEL_PATH)
        except Exception as exc:
            logger.warning("Voice model unavailable (%s); captions keep one style", exc)
            return None
    return MODEL_PATH


def _model():
    global _session
    if _session is None:
        import onnxruntime as ort

        path = ensure_model()
        if path is None:
            raise RuntimeError("voice model missing")
        _session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    return _session


def embed(samples: np.ndarray) -> Optional[np.ndarray]:
    """Unit-length voice vector for a stretch of 16 kHz speech; None when it is too short."""
    if len(samples) < MIN_EMBED_SECONDS * RATE:
        return None
    feats = fbank(samples)[None, :, :]
    vector = _model().run(None, {"x": feats})[0][0]
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else None


def cluster(vectors: list[np.ndarray], threshold: float = 0.35) -> list[int]:
    """Group voice vectors (average-link, cosine); labels numbered by first appearance."""
    groups = [[i] for i in range(len(vectors))]
    if not groups:
        return []
    sim = np.array([[float(np.dot(a, b)) for b in vectors] for a in vectors])
    while len(groups) > 1:
        best, pair = -2.0, None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                score = float(sim[np.ix_(groups[i], groups[j])].mean())
                if score > best:
                    best, pair = score, (i, j)
        if best < threshold or pair is None:
            break
        i, j = pair
        groups[i] += groups.pop(j)
    owner = {member: g for g, members in enumerate(groups) for member in members}
    order: dict[int, int] = {}
    return [order.setdefault(owner[i], len(order)) for i in range(len(vectors))]
