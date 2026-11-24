import numpy as np
import pytest

from src.media import asd


def test_mfcc_runs_at_100_frames_a_second_with_13_coefficients():
    rng = np.random.default_rng(3)
    feats = asd.mfcc(rng.standard_normal(16000).astype(np.float32), 16000)
    assert feats.shape[1] == 13 and 97 <= feats.shape[0] <= 101
    assert np.isfinite(feats).all()


def test_the_net_scores_each_video_frame():
    torch = pytest.importorskip("torch")
    net = asd.SpeakerNet()
    with torch.no_grad():
        scores = asd.frame_scores(net, np.random.rand(400, 13).astype(np.float32),
                                  np.random.randint(0, 255, (100, 112, 112), dtype=np.uint8), device="cpu")
    assert scores.shape == (100,) and (0 <= scores).all() and (scores <= 1).all()


def test_speaking_scores_average_the_spans(monkeypatch):
    monkeypatch.setattr(asd, "ensure_weights", lambda: "weights")
    monkeypatch.setattr(asd, "_net", lambda: (object(), "cpu"))
    monkeypatch.setattr(asd, "_faces", lambda clip, face: np.zeros((100, 112, 112), np.uint8))
    monkeypatch.setattr(asd, "_clip_audio", lambda clip: np.zeros(16000 * 4, np.float32))
    monkeypatch.setattr(asd, "mfcc", lambda audio, rate: np.zeros((400, 13), np.float32))
    per_frame = np.concatenate([np.full(50, 0.9), np.full(50, 0.1)]).astype(np.float32)  # 25 fps: 2 s + 2 s
    monkeypatch.setattr(asd, "frame_scores", lambda net, a, v, device: per_frame)
    scores = asd.speaking_scores("clip.mp4", (10, 10, 50, 50), [(0.0, 2.0), (2.0, 4.0), (3.5, 9.0)])
    assert scores is not None
    assert scores[0] > 0.85 and scores[1] < 0.15 and scores[2] < 0.15  # the tail clamps to the last frames


def test_speaking_scores_without_a_cam_or_weights_is_none(monkeypatch):
    assert asd.speaking_scores("clip.mp4", None, [(0.0, 1.0)]) is None
    monkeypatch.setattr(asd, "ensure_weights", lambda: None)
    assert asd.speaking_scores("clip.mp4", (0, 0, 10, 10), [(0.0, 1.0)]) is None
