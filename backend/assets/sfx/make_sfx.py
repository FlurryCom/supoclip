"""Synthesize the edit kits' sounds (CC0, ours): bell, boom, click, pop.

Run once after changing a recipe: python make_sfx.py
Everything is generated from math, so no third-party audio can trigger a
copyright claim.
"""
from pathlib import Path
import wave

import numpy as np

RATE = 48000
HERE = Path(__file__).resolve().parent


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(RATE * seconds)) / RATE


def _normalize(samples: np.ndarray, peak: float = 0.9) -> np.ndarray:
    return (samples / np.abs(samples).max() * peak).astype(np.float32)


def bell() -> np.ndarray:
    """Notification bell: inharmonic partials, each with its own decay."""
    t = _t(1.6)
    tone = sum(
        amp * np.sin(2 * np.pi * 1200 * ratio * t) * np.exp(-t / decay)
        for ratio, decay, amp in ((1, 1.2, 1.0), (2.76, 0.8, 0.5), (5.40, 0.5, 0.25), (8.93, 0.3, 0.12))
    )
    tone[: int(RATE * 0.002)] *= np.linspace(0, 1, int(RATE * 0.002))  # no click at the start
    return _normalize(tone)


def boom() -> np.ndarray:
    """Deep cinematic boom: a falling sine with a soft-clipped overtone."""
    t = _t(2.5)
    freq = 38 + (75 - 38) * np.exp(-t / 0.4)
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    body = np.sin(phase) + 0.35 * np.tanh(3 * np.sin(2 * phase))
    return _normalize(body * np.exp(-t / 1.5) * np.minimum(1, t / 0.005))


def click() -> np.ndarray:
    """Mouse click: a few milliseconds of bright noise with a sharp attack."""
    rng = np.random.default_rng(7)
    t = _t(0.03)
    noise = np.diff(rng.standard_normal(len(t) + 1))  # first difference = brighter
    return _normalize(noise * np.exp(-t / 0.004))


def pop() -> np.ndarray:
    """Button pop: a quick falling blip."""
    t = _t(0.12)
    freq = 300 + 600 * np.exp(-t / 0.02)
    return _normalize(np.sin(2 * np.pi * np.cumsum(freq) / RATE) * np.exp(-t / 0.035))


def write_wav(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())


if __name__ == "__main__":
    for name, make in (("bell", bell), ("boom", boom), ("click", click), ("pop", pop)):
        write_wav(HERE / f"{name}.wav", make())
