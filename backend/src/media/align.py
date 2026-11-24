"""When a line is actually said (Cat Clips 3.0).

GTA draws its subtitle 1–2 s before the voice starts, so a line timed by the drawing comes in
early (owner's complaint). Here a line's words are timed against the audio itself: forced
alignment with the local Whisper when it holds up, otherwise the first speech onset in the
window; silence keeps the drawn time.
Spec: docs/superpowers/specs/2026-11-24-caption-attribution-design.md.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

Word = tuple[str, float, float]  # word, start, end (clip seconds)
Aligner = Callable[[np.ndarray, str], Optional[list[Word]]]

HOP, WIN = 0.010, 0.025
SPEECH_WPS = 160 / 60  # plain speech; the gate allows 0.3-1.8x of it


def speech_onset(audio: np.ndarray, rate: int, start: float, end: float,
                 min_hold: float = 0.12) -> Optional[float]:
    """Where speech first holds for ``min_hold`` seconds inside the window, or None (silence)."""
    seg = audio[int(max(0.0, start) * rate):int(end * rate)]
    hop, win = int(HOP * rate), int(WIN * rate)
    if seg.size < win + hop:
        return None
    frames = np.lib.stride_tricks.sliding_window_view(seg, win)[::hop]
    db = 20 * np.log10(np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1)) + 1e-9)
    threshold = max(np.percentile(db, 20) + 9.0, -45.0)
    active = db > threshold
    need = max(1, int(round(min_hold / HOP)))
    run = np.convolve(active, np.ones(need), mode="valid")
    hits = np.flatnonzero(run >= need)
    return start + float(hits[0]) * HOP if hits.size else None


def align_text(audio: np.ndarray, rate: int, text: str, start: float, end: float,
               aligner: Optional[Aligner] = None) -> Optional[list[Word]]:
    """The text's words timed by forced alignment inside [start, end], or None when it can't be trusted."""
    aligner = aligner or _whisper_align
    seg = audio[int(max(0.0, start) * rate):int(end * rate)]
    if not seg.size:
        return None
    try:
        words = aligner(seg, text)
    except Exception as exc:
        logger.warning("Forced alignment failed (%s)", exc)
        return None
    expected = text.split()
    if not words or len(words) != len(expected):
        return None
    span = end - start
    last = 0.0
    for _, a, b in words:
        if a < last - 1e-6 or b < a - 1e-6 or a < -0.3 or b > span + 0.3:
            return None
        last = a
    said = words[-1][2] - words[0][1]
    estimate = len(expected) / SPEECH_WPS
    if not max(0.25, 0.3 * estimate) <= said <= 1.8 * estimate + 0.3:
        return None
    base = max(0.0, start)
    return [(w, round(base + a, 3), round(base + b, 3)) for (w, a, b), text_word in zip(words, expected)]


def line_times(audio: Optional[np.ndarray], rate: int, text: str, drawn: float, end: float,
               aligner: Optional[Aligner] = None, lead: float = 0.3, tail: float = 1.5) -> list[Word]:
    """Each word's time for a line drawn on screen over [drawn, end]: aligned to the audio when
    possible, else spread from the voice's onset; silence (or no audio) keeps the drawn time."""
    tokens = text.split()
    window_end = end + tail
    if audio is not None:
        aligned = align_text(audio, rate, text, max(0.0, drawn - lead), window_end, aligner)
        if aligned:
            return aligned
        onset = speech_onset(audio, rate, max(0.0, drawn - lead), window_end)
    else:
        onset = None
    start = onset if onset is not None else drawn
    length = min(max(len(tokens) / SPEECH_WPS, 0.4), max(window_end - start, 0.4))
    step = length / len(tokens)
    return [(w, round(start + k * step, 3), round(start + (k + 1) * step, 3)) for k, w in enumerate(tokens)]


def _whisper_align(seg: np.ndarray, text: str, language: str = "en") -> Optional[list[Word]]:
    """Word times from the local Whisper's cross-attention alignment of the known text."""
    import whisper
    from whisper.timing import find_alignment
    from whisper.tokenizer import get_tokenizer

    from .transcription import _get_whisper_model

    model = _get_whisper_model("base")
    tokenizer = get_tokenizer(model.is_multilingual, num_languages=model.num_languages,
                              language=language, task="transcribe")
    mel = whisper.log_mel_spectrogram(whisper.pad_or_trim(np.ascontiguousarray(seg, dtype=np.float32)),
                                      model.dims.n_mels)
    timings = find_alignment(model, tokenizer, tokenizer.encode(" " + text.strip()),
                             mel.to(model.device), min(len(seg) // (rate_frames := 160), mel.shape[-1]))
    words = [(t.word.strip(), float(t.start), float(t.end)) for t in timings if t.word.strip()]
    return words or None
