import subprocess

import numpy as np

from src.media import music_check as mc


def test_music_spans_joins_short_gaps_and_drops_blips():
    probs = np.array([.1, .8, .8, .1, .8, .8, .8, .1, .1, .1, .9, .1])
    assert mc.music_spans(probs) == [(1.0, 7.0)]
    assert mc.music_spans(np.zeros(5)) == []
    assert mc.music_spans(np.array([.9, .9, .9])) == [(0.0, 3.0)]


def test_mix_spans_swaps_only_music_stretches():
    rate = 10
    original = np.ones((2, 80), dtype=np.float32)
    vocals = np.zeros((2, 80), dtype=np.float32)
    out = mc.mix_spans(original, vocals, [(0.0, 2.0), (5.0, 6.0)], rate, fade=0.3)
    assert out.shape == original.shape
    assert np.all(out[:, :20] == 0)  # a stretch at the very start works
    assert np.all(out[:, 23:47] == 1)  # between the stretches, after the fades: the original
    assert np.all(out[:, 50:60] == 0)  # inside the second stretch: only the voices
    assert np.all(out[:, 63:] == 1)
    assert 0 < out[0, 21] < 1  # a soft change, not a cut


def _clip(tmp_path, audio="sine=frequency=440:duration=6"):
    path = tmp_path / "clip.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=10:d=6"]
    if audio:
        cmd += ["-f", "lavfi", "-i", audio, "-c:a", "aac", "-shortest"]
    subprocess.run(cmd + ["-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _duration(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True, check=True).stdout)


def _video_packets(path):
    return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "packet=size",
                           "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout


def test_clip_without_audio_is_clean(tmp_path):
    path = _clip(tmp_path, audio=None)
    before = path.read_bytes()
    assert mc.clean_clip(path) == {"status": "clean", "music_seconds": 0.0, "error": None}
    assert path.read_bytes() == before


def test_no_music_leaves_the_file_as_is(tmp_path, monkeypatch):
    path = _clip(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(mc, "music_per_second", lambda mono: np.zeros(6))
    assert mc.clean_clip(path)["status"] == "clean"
    assert path.read_bytes() == before


def _music_then(after):
    calls = iter([np.full(6, .9), after])
    return lambda mono: next(calls)


def test_music_is_swapped_for_vocals(tmp_path, monkeypatch):
    path = _clip(tmp_path)
    video, duration = _video_packets(path), _duration(path)
    monkeypatch.setattr(mc, "music_per_second", _music_then(np.zeros(6)))
    monkeypatch.setattr(mc, "separate_vocals", lambda stereo: np.zeros_like(stereo))
    assert mc.clean_clip(path) == {"status": "removed", "music_seconds": 6.0, "error": None}
    assert _video_packets(path) == video  # the picture is copied, never re-encoded
    assert abs(_duration(path) - duration) < 0.1
    loud = subprocess.run(["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                          capture_output=True, text=True).stderr
    assert "max_volume: -91" in loud  # the tone (the "music") is gone


def test_music_that_survives_is_left(tmp_path, monkeypatch):
    path = _clip(tmp_path)
    monkeypatch.setattr(mc, "music_per_second", _music_then(np.full(6, .9)))
    monkeypatch.setattr(mc, "separate_vocals", lambda stereo: stereo)
    assert mc.clean_clip(path)["status"] == "left"


def test_short_leftovers_still_count_as_removed(tmp_path, monkeypatch):
    # Real clips: a violin bleeding through for 2 s, a sung-sounding phrase; too short for Content ID.
    path = _clip(tmp_path)
    monkeypatch.setattr(mc, "music_per_second", _music_then(np.array([.9, .9, .1, .1, .6, .1])))
    monkeypatch.setattr(mc, "separate_vocals", lambda stereo: stereo)
    assert mc.clean_clip(path)["status"] == "removed"


def test_a_failure_leaves_the_clip_untouched(tmp_path, monkeypatch):
    path = _clip(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(mc, "music_per_second", lambda mono: np.full(6, .9))

    def broken(stereo):
        raise RuntimeError("onnx down")

    monkeypatch.setattr(mc, "separate_vocals", broken)
    result = mc.clean_clip(path)
    assert result["status"] == "error" and "onnx down" in result["error"]
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]  # no temporary file left behind


class _Body:
    def __init__(self, data):
        self.data = data

    def read(self, n=-1):
        out, self.data = self.data[:n], self.data[n:]
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_bad_download_leaves_no_model(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "MODELS_DIR", tmp_path / "music")
    monkeypatch.setattr(mc.urllib.request, "urlopen", lambda url, timeout: _Body(b"not a model"))
    assert mc.ensure_model("cnn10.pth") is None
    assert not list((tmp_path / "music").glob("*"))
