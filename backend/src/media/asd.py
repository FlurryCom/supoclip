"""Is the webcam face the one talking? (Cat Clips 3.0 active speaker detection.)

The net is Light-ASD (CVPR 2023), vendored from github.com/Junhua-Liao/Light-ASD
(MIT, (c) 2023 Liao Junhua), weights fine-tuned on TalkSet for in-the-wild video, downloaded
at runtime like the voice model. The MFCC follows python_speech_features' defaults
(MIT, (c) 2013 James Lyons), which the weights were trained against.
Spec: docs/superpowers/specs/2026-11-24-caption-attribution-design.md.
"""
from __future__ import annotations

import logging
import math
import os
import subprocess
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

FPS = 25  # the model's clock: 4 MFCC frames (100/s) per video frame
SIDE = 112
WEIGHTS_PATH = Path(os.getenv("ASD_MODEL_PATH", "/app/uploads/models/asd/finetuning_TalkSet.model"))
WEIGHTS_URL = "https://raw.githubusercontent.com/Junhua-Liao/Light-ASD/main/weight/finetuning_TalkSet.model"
_session = None


# --- MFCC (python_speech_features defaults: winlen 0.025, winstep 0.010, 26 filters, NFFT 512) ---

def _mel_filters(rate: int, nfft: int = 512, nfilt: int = 26) -> np.ndarray:
    def to_mel(hz):
        return 2595 * np.log10(1 + np.asarray(hz) / 700.0)

    def to_hz(mel):
        return 700 * (10 ** (np.asarray(mel) / 2595.0) - 1)

    points = np.floor((nfft + 1) * to_hz(np.linspace(0, to_mel(rate / 2), nfilt + 2)) / rate).astype(int)
    bank = np.zeros((nfilt, nfft // 2 + 1))
    for j in range(nfilt):
        a, b, c = points[j], points[j + 1], points[j + 2]
        for i in range(a, b):
            bank[j, i] = (i - a) / max(1, b - a)
        for i in range(b, c):
            bank[j, i] = (c - i) / max(1, c - b)
    return bank


def _dct_ortho(n_out: int, n_in: int) -> np.ndarray:
    k = np.arange(n_out)[:, None]
    n = np.arange(n_in)[None, :]
    table = np.cos(np.pi * k * (2 * n + 1) / (2 * n_in)) * math.sqrt(2 / n_in)
    table[0] /= math.sqrt(2)
    return table


def mfcc(audio: np.ndarray, rate: int) -> np.ndarray:
    """13 MFCCs per 10 ms frame, python_speech_features-compatible (energy in c0, liftered)."""
    signal = np.append(audio[0], audio[1:] - 0.97 * audio[:-1]).astype(np.float64)
    frame_len, step, nfft = int(0.025 * rate), int(0.010 * rate), 512
    count = 1 if len(signal) <= frame_len else 1 + math.ceil((len(signal) - frame_len) / step)
    padded = np.concatenate([signal, np.zeros(max(0, (count - 1) * step + frame_len - len(signal)))])
    frames = np.lib.stride_tricks.sliding_window_view(padded, frame_len)[::step][:count]
    power = (np.abs(np.fft.rfft(frames, nfft)) ** 2) / nfft
    energy = np.maximum(power.sum(axis=1), np.finfo(np.float64).eps)
    feat = np.log(np.maximum(power @ _mel_filters(rate).T, np.finfo(np.float64).eps))
    feat = feat @ _dct_ortho(13, feat.shape[1]).T
    lift = 1 + 11 * np.sin(np.pi * np.arange(13) / 22)
    feat *= lift
    feat[:, 0] = np.log(energy)
    return feat.astype(np.float32)


# --- Light-ASD net (vendored) ---

def _torch():
    import torch
    import torch.nn as nn
    return torch, nn


def _build_net():
    torch, nn = _torch()

    class AudioBlock(nn.Module):
        def __init__(self, cin, cout):
            super().__init__()
            self.relu = nn.ReLU()
            self.m_3 = nn.Conv2d(cin, cout, (3, 1), padding=(1, 0), bias=False)
            self.bn_m_3 = nn.BatchNorm2d(cout, momentum=0.01, eps=0.001)
            self.t_3 = nn.Conv2d(cout, cout, (1, 3), padding=(0, 1), bias=False)
            self.bn_t_3 = nn.BatchNorm2d(cout, momentum=0.01, eps=0.001)
            self.m_5 = nn.Conv2d(cin, cout, (5, 1), padding=(2, 0), bias=False)
            self.bn_m_5 = nn.BatchNorm2d(cout, momentum=0.01, eps=0.001)
            self.t_5 = nn.Conv2d(cout, cout, (1, 5), padding=(0, 2), bias=False)
            self.bn_t_5 = nn.BatchNorm2d(cout, momentum=0.01, eps=0.001)
            self.last = nn.Conv2d(cout, cout, 1, bias=False)
            self.bn_last = nn.BatchNorm2d(cout, momentum=0.01, eps=0.001)

        def forward(self, x):
            x3 = self.relu(self.bn_t_3(self.t_3(self.relu(self.bn_m_3(self.m_3(x))))))
            x5 = self.relu(self.bn_t_5(self.t_5(self.relu(self.bn_m_5(self.m_5(x))))))
            return self.relu(self.bn_last(self.last(x3 + x5)))

    class VisualBlock(nn.Module):
        def __init__(self, cin, cout, is_down=False):
            super().__init__()
            self.relu = nn.ReLU()
            stride = (1, 2, 2) if is_down else (1, 1, 1)
            self.s_3 = nn.Conv3d(cin, cout, (1, 3, 3), stride=stride, padding=(0, 1, 1), bias=False)
            self.bn_s_3 = nn.BatchNorm3d(cout, momentum=0.01, eps=0.001)
            self.t_3 = nn.Conv3d(cout, cout, (3, 1, 1), padding=(1, 0, 0), bias=False)
            self.bn_t_3 = nn.BatchNorm3d(cout, momentum=0.01, eps=0.001)
            self.s_5 = nn.Conv3d(cin, cout, (1, 5, 5), stride=stride, padding=(0, 2, 2), bias=False)
            self.bn_s_5 = nn.BatchNorm3d(cout, momentum=0.01, eps=0.001)
            self.t_5 = nn.Conv3d(cout, cout, (5, 1, 1), padding=(2, 0, 0), bias=False)
            self.bn_t_5 = nn.BatchNorm3d(cout, momentum=0.01, eps=0.001)
            self.last = nn.Conv3d(cout, cout, 1, bias=False)
            self.bn_last = nn.BatchNorm3d(cout, momentum=0.01, eps=0.001)

        def forward(self, x):
            x3 = self.relu(self.bn_t_3(self.t_3(self.relu(self.bn_s_3(self.s_3(x))))))
            x5 = self.relu(self.bn_t_5(self.t_5(self.relu(self.bn_s_5(self.s_5(x))))))
            return self.relu(self.bn_last(self.last(x3 + x5)))

    class VisualEncoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.block1 = VisualBlock(1, 32, is_down=True)
            self.pool1 = nn.MaxPool3d((1, 3, 3), stride=(1, 2, 2), padding=(0, 1, 1))
            self.block2 = VisualBlock(32, 64)
            self.pool2 = nn.MaxPool3d((1, 3, 3), stride=(1, 2, 2), padding=(0, 1, 1))
            self.block3 = VisualBlock(64, 128)
            self.maxpool = nn.AdaptiveMaxPool2d((1, 1))

        def forward(self, x):
            x = self.pool1(self.block1(x))
            x = self.pool2(self.block2(x))
            x = self.block3(x).transpose(1, 2)
            b, t, c, w, h = x.shape
            return self.maxpool(x.reshape(b * t, c, w, h)).view(b, t, c)

    class AudioEncoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.block1 = AudioBlock(1, 32)
            self.pool1 = nn.MaxPool3d((1, 1, 3), stride=(1, 1, 2), padding=(0, 0, 1))
            self.block2 = AudioBlock(32, 64)
            self.pool2 = nn.MaxPool3d((1, 1, 3), stride=(1, 1, 2), padding=(0, 0, 1))
            self.block3 = AudioBlock(64, 128)

        def forward(self, x):
            x = self.pool1(self.block1(x))
            x = self.pool2(self.block2(x))
            x = self.block3(x)
            return x.mean(dim=2, keepdim=True).squeeze(2).transpose(1, 2)

    class BGRU(nn.Module):
        def __init__(self, channel):
            super().__init__()
            self.gru_forward = nn.GRU(channel, channel, 1, batch_first=True)
            self.gru_backward = nn.GRU(channel, channel, 1, batch_first=True)
            self.gelu = nn.GELU()

        def forward(self, x):
            x = self.gelu(self.gru_forward(x)[0])
            x = torch.flip(self.gru_backward(torch.flip(x, dims=[1]))[0], dims=[1])
            return self.gelu(x)

    class ASDModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.visualEncoder = VisualEncoder()
            self.audioEncoder = AudioEncoder()
            self.GRU = BGRU(128)

    class LossHead(nn.Module):
        def __init__(self):
            super().__init__()
            self.FC = nn.Linear(128, 2)

    class SpeakerNet(nn.Module):
        """The checkpoint's layout: ``model.*``, ``lossAV.*``, ``lossV.*``."""

        def __init__(self):
            super().__init__()
            self.model = ASDModel()
            self.lossAV = LossHead()
            self.lossV = LossHead()

        def forward(self, audio, video):
            a = self.model.audioEncoder(audio.unsqueeze(1).transpose(2, 3))
            v = self.model.visualEncoder((((video / 255) - 0.4161) / 0.1688).unsqueeze(1))
            t = min(a.shape[1], v.shape[1])
            x = self.model.GRU(a[:, :t] + v[:, :t]).reshape(-1, 128)
            return torch.softmax(self.lossAV.FC(x), dim=-1)[:, 1]

    return SpeakerNet


def SpeakerNet():
    return _build_net()().eval()  # inference only


def ensure_weights() -> Optional[Path]:
    """The Light-ASD weights on disk, downloaded once (None when the download fails)."""
    if WEIGHTS_PATH.exists():
        return WEIGHTS_PATH
    try:
        import urllib.request

        WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        part = WEIGHTS_PATH.with_suffix(".part")
        urllib.request.urlretrieve(WEIGHTS_URL, part)  # noqa: S310 (fixed https URL)
        part.replace(WEIGHTS_PATH)
        return WEIGHTS_PATH
    except Exception as exc:
        logger.warning("Could not fetch the ASD weights (%s)", exc)
        return None


def _net():
    global _session
    if _session is None:
        torch, _ = _torch()
        path = ensure_weights()
        if path is None:
            raise RuntimeError("ASD weights missing")
        net = SpeakerNet()
        state = torch.load(path, map_location="cpu", weights_only=True)  # tensors only, never pickled code
        net.load_state_dict({k.replace("module.", ""): v for k, v in state.items()}, strict=False)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _session = (net.to(device), device)
    return _session


DURATIONS = (2, 4, 6)  # seconds; the model was trained on short windows, and chunks keep the GPU small


def frame_scores(net, audio_feats: np.ndarray, video: np.ndarray, device: str = "cpu") -> np.ndarray:
    """P(the face speaks) per video frame (25/s): windows of a few seconds, ensembled like the demo."""
    torch, _ = _torch()
    frames = min((audio_feats.shape[0] - audio_feats.shape[0] % 4) // 4, video.shape[0])

    def one_pass(duration: int) -> np.ndarray:
        pieces = []
        for start in range(0, frames, duration * FPS):
            stop = min(start + duration * FPS, frames)
            with torch.no_grad():
                scores = net(torch.from_numpy(audio_feats[start * 4:stop * 4]).float().unsqueeze(0).to(device),
                             torch.from_numpy(video[start:stop].astype(np.float32)).unsqueeze(0).to(device))
            pieces.append(scores.detach().cpu().numpy().reshape(-1))
        return np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)

    out = np.mean([one_pass(d) for d in DURATIONS], axis=0)
    if out.size >= 5:  # the demo's ±2-frame smoothing
        out = np.convolve(out, np.ones(5) / 5, mode="same")
    return out


def _faces(clip, face) -> Optional[np.ndarray]:
    """The webcam face at 25 fps, cropped the way the training pipeline framed faces."""
    x, y, w, h = face
    # The detector's box is tighter than the training pipeline's face: pad it so the whole
    # face (chin included) lands in frame, mouth slightly below centre as in training.
    half = 1.3 * max(w, h) / 2
    cx, cy = x + w / 2, y + h / 2 + 0.1 * max(w, h)
    left, top = max(0, int(cx - half)), max(0, int(cy - half))
    side = max(8, int(2 * half))
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(clip),
         "-vf", f"crop={side}:{side}:{left}:{top},scale={SIDE}:{SIDE},format=gray,fps={FPS}",
         "-f", "rawvideo", "-"],
        capture_output=True, check=True, timeout=600,
    ).stdout
    count = len(raw) // (SIDE * SIDE)
    if not count:
        return None
    return np.frombuffer(raw[:count * SIDE * SIDE], dtype=np.uint8).reshape(count, SIDE, SIDE)


def _clip_audio(clip) -> np.ndarray:
    from .voice_roles import _audio

    return _audio(clip)


def speaking_scores(clip, face, spans: Sequence[tuple[float, float]]) -> Optional[list[float]]:
    """Mean P(the webcam face speaks) per (start, end) span, or None when it can't be measured."""
    if face is None or ensure_weights() is None:
        return None
    try:
        video = _faces(clip, face)
        if video is None:
            return None
        net, device = _net()
        feats = mfcc(_clip_audio(clip), 16000)
        try:
            per_frame = frame_scores(net, feats, video, device)
        except RuntimeError as exc:
            if "out of memory" not in str(exc).lower():
                raise
            logger.warning("ASD fell off the GPU (%s); scoring on the CPU", exc)
            global _session
            per_frame = frame_scores(net.cpu(), feats, video, "cpu")
            _session = (net, "cpu")
        out = []
        for start, end in spans:
            a = min(max(0, int(start * FPS)), len(per_frame) - 1)
            b = min(max(a + 1, int(math.ceil(end * FPS))), len(per_frame))
            out.append(round(float(per_frame[a:b].mean()), 3))
        return out
    except Exception as exc:
        logger.warning("ASD scoring failed (%s)", exc)
        return None
