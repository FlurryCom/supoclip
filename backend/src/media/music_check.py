"""Copyright music (fork feature): find music in a rendered clip and keep only the voices there.

Game radio and songs on stream get clips blocked. A small AudioSet classifier
(PANNs Cnn10, torch) gives the chance of music each second; where there is
music, an MDX-Net vocal model (Kim_Vocal_2, onnxruntime) keeps the voices and
the clip's audio is swapped for them in those stretches only. If music is
still heard afterwards (usually sung music), the clip is "left" and the
recorder never posts it. Any failure leaves the clip as it was ("error").
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

MODELS_DIR = Path(os.getenv("MUSIC_MODELS_DIR", "/app/uploads/models/music"))
MODELS = {
    "cnn10.pth": ("https://zenodo.org/api/records/3987831/files/Cnn10_mAP=0.380.pth/content",
                  "5240bdc47444e331bbeb54fd741d2b3f933a4526e84b0c17bf3593df94b13962"),
    "Kim_Vocal_2.onnx": ("https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx",
                         "ce74ef3b6a6024ce44211a07be9cf8bc6d87728cc852a68ab34eb8e58cde9c8b"),
}
MUSIC_CLASS = 137  # AudioSet "Music"
# What still counts as music after the cleanup: real clips kept 1-2 s blips (a violin bleeding through,
# a sung-sounding phrase); only a steady stretch is enough for a copyright match.
LEFT_THRESHOLD, LEFT_MIN_SECONDS = 0.5, 4
DETECT_RATE, SEPARATE_RATE = 32000, 44100
N_FFT, HOP, DIM_F, DIM_T = 7680, 1024, 3072, 256  # Kim_Vocal_2's spectrogram
CHUNK = HOP * (DIM_T - 1)
TRIM = N_FFT // 2
GEN = CHUNK - 2 * TRIM

_lock = threading.Lock()  # one separation at a time per worker: each peaks near 2 GB
_detector: Optional[dict] = None
_separator = None


def ensure_model(name: str) -> Optional[Path]:
    """The model on disk, downloaded and checked once; None when it can't be had."""
    path = MODELS_DIR / name
    if path.exists():
        return path
    url, sha256 = MODELS[name]
    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=MODELS_DIR, suffix=".part", delete=False) as part:
            digest = hashlib.sha256()
            with urllib.request.urlopen(url, timeout=30) as response:
                while chunk := response.read(1 << 20):
                    part.write(chunk)
                    digest.update(chunk)
        if digest.hexdigest() != sha256:
            raise ValueError(f"{name} checksum mismatch")
        os.replace(part.name, path)
        return path
    except Exception as exc:
        logger.warning("Music model %s unavailable (%s)", name, exc)
        if "part" in locals():
            Path(part.name).unlink(missing_ok=True)
        return None


def _model_path(name: str) -> Path:
    path = ensure_model(name)
    if path is None:
        raise RuntimeError(f"music model {name} missing")
    return path


def _weights() -> dict:
    global _detector
    if _detector is None:
        import torch

        path = _model_path("cnn10.pth")
        try:
            _detector = torch.load(path, map_location="cpu", weights_only=True)["model"]
        except Exception:
            path.unlink(missing_ok=True)  # a broken file would fail every clip; fetch it again next time
            raise
    return _detector


def _cnn10(wav):
    """PANNs Cnn10 forward pass: (batch, samples at 32 kHz) -> (batch, 527) class chances."""
    import torch
    import torch.nn.functional as F

    w = _weights()

    def bn(x, p):
        return F.batch_norm(x, w[p + ".running_mean"], w[p + ".running_var"], w[p + ".weight"], w[p + ".bias"], False, 0.0, 1e-5)

    x = F.pad(wav[:, None, :], (512, 512), mode="reflect")
    real = F.conv1d(x, w["spectrogram_extractor.stft.conv_real.weight"], stride=320)
    imag = F.conv1d(x, w["spectrogram_extractor.stft.conv_imag.weight"], stride=320)
    mel = (real ** 2 + imag ** 2).transpose(1, 2) @ w["logmel_extractor.melW"]
    x = 10 * torch.log10(torch.clamp(mel, min=1e-10))[:, None]
    x = bn(x.transpose(1, 3), "bn0").transpose(1, 3)
    for i in range(1, 5):
        p = f"conv_block{i}"
        x = F.relu(bn(F.conv2d(x, w[p + ".conv1.weight"], padding=1), p + ".bn1"))
        x = F.relu(bn(F.conv2d(x, w[p + ".conv2.weight"], padding=1), p + ".bn2"))
        x = F.avg_pool2d(x, 2)
    x = x.mean(3)
    x = x.max(2)[0] + x.mean(2)
    x = F.relu(F.linear(x, w["fc1.weight"], w["fc1.bias"]))
    return torch.sigmoid(F.linear(x, w["fc_audioset.weight"], w["fc_audioset.bias"]))


def music_per_second(mono32: np.ndarray) -> np.ndarray:
    """Chance of music for each whole second, from a 2 s window centred on it."""
    import torch

    seconds = len(mono32) // DETECT_RATE
    windows = []
    for s in range(seconds):
        start = int(max(0.0, s - 0.5) * DETECT_RATE)
        window = mono32[start:start + 2 * DETECT_RATE]
        windows.append(np.pad(window, (0, 2 * DETECT_RATE - len(window))))
    out = []
    with torch.no_grad():
        for i in range(0, len(windows), 16):
            out.append(_cnn10(torch.from_numpy(np.stack(windows[i:i + 16])))[:, MUSIC_CLASS].numpy())
    return np.concatenate(out) if out else np.zeros(0)


def music_spans(probs: np.ndarray, threshold: float = 0.3, max_gap: int = 2, min_len: int = 2) -> list[tuple[float, float]]:
    """Stretches of music in seconds; gaps of up to ``max_gap`` s join, stretches under ``min_len`` s drop."""
    runs: list[list[int]] = []
    for s in np.flatnonzero(np.asarray(probs) > threshold):
        if runs and s - runs[-1][1] <= max_gap + 1:
            runs[-1][1] = int(s)
        else:
            runs.append([int(s), int(s)])
    return [(float(a), float(b + 1)) for a, b in runs if b + 1 - a >= min_len]


def _separator_session():
    global _separator
    if _separator is None:
        import onnxruntime as ort

        path = _model_path("Kim_Vocal_2.onnx")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 6
        options.enable_cpu_mem_arena = False  # the arena kept ~1 GB more after each chunk
        options.enable_mem_pattern = False
        try:
            _separator = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return _separator


def separate_vocals(stereo44: np.ndarray) -> np.ndarray:
    """The voices of a (2, samples) 44.1 kHz track, same shape (MDX-Net, overlapping chunks)."""
    import torch

    session = _separator_session()
    window = torch.hann_window(N_FFT, periodic=True)
    n = stereo44.shape[1]
    pad = GEN - n % GEN
    mix = np.concatenate([np.zeros((2, TRIM), np.float32), stereo44.astype(np.float32),
                          np.zeros((2, pad + TRIM), np.float32)], 1)
    out = []
    for i in range(0, n + pad, GEN):
        spec = torch.stft(torch.from_numpy(mix[:, i:i + CHUNK]), N_FFT, HOP, window=window, center=True, return_complex=True)
        spec = torch.view_as_real(spec).permute(0, 3, 1, 2).reshape(1, 4, -1, DIM_T)[:, :, :DIM_F]
        voices = torch.from_numpy(session.run(None, {"input": spec.numpy()})[0])
        voices = torch.cat([voices, torch.zeros(1, 4, N_FFT // 2 + 1 - DIM_F, DIM_T)], 2)
        voices = voices.reshape(2, 2, -1, DIM_T).permute(0, 2, 3, 1).contiguous()
        wav = torch.istft(torch.view_as_complex(voices), N_FFT, HOP, window=window, center=True)
        out.append(wav[:, TRIM:TRIM + GEN].numpy())
    return np.concatenate(out, 1)[:, :n]


def mix_spans(original: np.ndarray, vocals: np.ndarray, spans, rate: int, fade: float = 0.3) -> np.ndarray:
    """Voices inside the music stretches, the original elsewhere, with a short crossfade outside each edge."""
    n = original.shape[1]
    weight = np.zeros(n, np.float32)
    f = max(1, int(fade * rate))
    ramp = np.linspace(0, 1, f + 2, dtype=np.float32)[1:-1]
    for start, end in spans:
        s, e = int(start * rate), min(n, int(end * rate))
        weight[s:e] = 1
        a = max(0, s - f)
        weight[a:s] = np.maximum(weight[a:s], ramp[f - (s - a):])
        b = min(n, e + f)
        weight[e:b] = np.maximum(weight[e:b], ramp[::-1][:b - e])
    return original * (1 - weight) + vocals * weight


def _has_audio(path: Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True, timeout=60).stdout
    return bool(out.strip())


def _audio(path: Path, rate: int, channels: int) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-vn", "-ac", str(channels), "-ar", str(rate),
                          "-f", "f32le", "-"], capture_output=True, check=True, timeout=600).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, channels).T.copy()


def _swap_audio(path: Path, stereo44: np.ndarray, out: Path) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path),
                    "-f", "f32le", "-ar", str(SEPARATE_RATE), "-ac", "2", "-i", "-",
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", str(out)],
                   input=np.ascontiguousarray(stereo44.T, dtype=np.float32).tobytes(), check=True, timeout=600)


def clean_clip(path: Path) -> dict[str, Any]:
    """Swap music stretches for their voices in place; {"status", "music_seconds", "error"}. Never raises."""
    path = Path(path)
    temp: Optional[Path] = None
    try:
        if not _has_audio(path):
            return {"status": "clean", "music_seconds": 0.0, "error": None}
        spans = music_spans(music_per_second(_audio(path, DETECT_RATE, 1)[0]))
        if not spans:
            return {"status": "clean", "music_seconds": 0.0, "error": None}
        seconds = float(sum(end - start for start, end in spans))
        stereo = _audio(path, SEPARATE_RATE, 2)
        with _lock:
            vocals = separate_vocals(stereo)
        mixed = mix_spans(stereo, vocals, spans, SEPARATE_RATE)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=path.suffix)
        os.close(fd)
        temp = Path(name)
        _swap_audio(path, mixed, temp)
        # Check again, only where music was: what still sounds like music there survived the separation.
        after = music_per_second(_audio(temp, DETECT_RATE, 1)[0])
        inside = np.zeros_like(after)
        for start, end in spans:
            inside[int(start):int(end)] = after[int(start):int(end)]
        status = "left" if music_spans(inside, LEFT_THRESHOLD, max_gap=1, min_len=LEFT_MIN_SECONDS) else "removed"
        os.replace(temp, path)
        temp = None
        return {"status": status, "music_seconds": seconds, "error": None}
    except Exception as exc:
        logger.warning("Music check failed for %s: %s", path.name, exc)
        return {"status": "error", "music_seconds": 0.0, "error": f"{exc}"[:300]}
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


async def save_result(file_path: str, result: dict[str, Any]) -> None:
    from sqlalchemy import text

    from ..database import get_session_maker

    async with get_session_maker()() as db:
        await db.execute(text("""
            INSERT INTO live_clip_music (file_path, status, music_seconds, error, checked_at)
            VALUES (:file_path, :status, :music_seconds, :error, now())
            ON CONFLICT (file_path) DO UPDATE SET status = EXCLUDED.status, music_seconds = EXCLUDED.music_seconds,
                error = EXCLUDED.error, checked_at = now()
        """), {"file_path": file_path, **result})
        await db.commit()


async def _was_checked(file_paths: list[str]) -> bool:
    from sqlalchemy import text

    from ..database import get_session_maker

    async with get_session_maker()() as db:
        row = (await db.execute(text("SELECT 1 FROM live_clip_music WHERE file_path = ANY(:paths) LIMIT 1"),
                                {"paths": list(file_paths)})).first()
    return row is not None


async def recheck(old_paths: list[str], new_path: Path) -> None:
    """An edited clip (trim, split, merge, captions) gets the music check its source had; never raises."""
    import asyncio

    try:
        if await _was_checked(old_paths):
            await save_result(str(new_path), await asyncio.to_thread(clean_clip, Path(new_path)))
    except Exception as exc:
        logger.warning("Music recheck failed for %s: %s", Path(new_path).name, exc)
