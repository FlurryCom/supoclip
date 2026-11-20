import numpy as np

from src.media import voice_roles as vr


def word(start, end, text="hi"):
    return {"start": int(start * 1000), "end": int(end * 1000), "text": text}


def test_speech_segments_split_on_pauses():
    words = [word(0, 1), word(1.1, 2), word(3, 4)]
    assert vr.speech_segments(words) == [(0.0, 2.0, [0, 1]), (3.0, 4.0, [2])]
    assert vr.speech_segments([]) == []


def test_fbank_shape():
    t = np.arange(16000) / 16000
    feats = vr.fbank(np.sin(2 * np.pi * 440 * t).astype(np.float32))
    assert 97 <= feats.shape[0] <= 100 and feats.shape[1] == 80
    assert abs(float(feats.mean())) < 1e-3  # global mean removed, as the model expects


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_cluster_groups_same_voice():
    a, a2, b = _unit([1, 0, 0.1]), _unit([0.95, 0.05, 0.12]), _unit([0, 1, 0])
    assert vr.cluster([a, a2, b]) == [0, 0, 1]
    assert vr.cluster([a]) == [0]
    assert vr.cluster([]) == []


def test_embed_is_none_for_short_audio():
    assert vr.embed(np.zeros(8000, dtype=np.float32)) is None  # 0.5 s
