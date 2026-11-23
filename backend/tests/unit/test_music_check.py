import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.media import music_check as mc

COMMERCIAL = {"title": "Raining Blood", "artist": "Slayer", "label": "American Recordings"}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, tmp_path_factory):
    # Every test: any music found is a recognized commercial song (today's behaviour), no free tracks.
    monkeypatch.setattr(mc, "identify_span", lambda path, start, end: COMMERCIAL)
    monkeypatch.setattr(mc, "singing_per_second", lambda mono: np.zeros(64))
    monkeypatch.setattr(mc, "MUSIC_BED_DIR", tmp_path_factory.mktemp("no-bed"))


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


def _recheck_fakes(monkeypatch, checked):
    calls = []

    async def was_checked(paths):
        calls.append(("was_checked", list(paths)))
        return checked

    async def save(file_path, result):
        calls.append(("save", file_path, result["status"]))

    monkeypatch.setattr(mc, "_was_checked", was_checked)
    monkeypatch.setattr(mc, "save_result", save)
    monkeypatch.setattr(mc, "clean_clip", lambda path: {"status": "removed", "music_seconds": 5.0, "error": None})
    return calls


def test_an_edited_clip_is_checked_again(tmp_path, monkeypatch):
    # Editing captions re-renders from the source: without this the radio would come back unchecked.
    import asyncio
    calls = _recheck_fakes(monkeypatch, checked=True)
    asyncio.run(mc.recheck(["/app/uploads/clips/old.mp4"], tmp_path / "new.mp4"))
    assert calls == [("was_checked", ["/app/uploads/clips/old.mp4"]), ("save", str(tmp_path / "new.mp4"), "removed")]


def test_clips_never_checked_stay_unchecked_after_an_edit(tmp_path, monkeypatch):
    import asyncio
    calls = _recheck_fakes(monkeypatch, checked=False)
    asyncio.run(mc.recheck(["a.mp4", "b.mp4"], tmp_path / "new.mp4"))
    assert calls == [("was_checked", ["a.mp4", "b.mp4"])]



SPAN = [(0.0, 6.0)]
NAMES = ["NoCopyrightSounds", "NCS", "StreamBeats"]


def _decide(identify, singing=np.zeros(6), spans=SPAN):
    return mc.decide_spans(spans, identify, singing, NAMES)[0]


def test_recognized_commercial_is_removed():
    assert _decide(lambda s, e: COMMERCIAL) == SPAN


def test_allowed_artist_is_kept():
    assert _decide(lambda s, e: {"title": "Sky High", "artist": "Elektronomia", "label": "NCS"}) == []


def test_unrecognized_is_kept():
    assert _decide(lambda s, e: None) == []


def test_shazam_down_falls_back_to_singing():
    def down(s, e):
        raise TimeoutError("shazam")
    assert _decide(down, singing=np.array([.9, .9, .9, 0, 0, 0])) == SPAN  # sung: out
    assert _decide(down, singing=np.zeros(6)) == []  # instrumental: stays


def test_short_spans_follow_todays_rule():
    assert _decide(lambda s, e: None, spans=[(1.0, 3.0)]) == [(1.0, 3.0)]


def test_allowlist_ignores_blanks_and_comments(tmp_path):
    path = tmp_path / "allow.txt"
    path.write_text("# liberados\n\n  NCS  \nStreamBeats\n")
    assert mc.allowed_names(path) == ["NCS", "StreamBeats"]
    assert mc.allowed_names(tmp_path / "missing.txt") == NAMES


def test_all_spans_kept_leaves_the_clip_alone(tmp_path, monkeypatch):
    path = _clip(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(mc, "music_per_second", lambda mono: np.full(6, .9))
    monkeypatch.setattr(mc, "identify_span", lambda p, s, e: None)
    assert mc.clean_clip(path) == {"status": "kept", "music_seconds": 6.0, "error": None}
    assert path.read_bytes() == before


def test_no_bed_tracks_just_removes(tmp_path, monkeypatch):
    path = _clip(tmp_path)
    monkeypatch.setattr(mc, "music_per_second", _music_then(np.zeros(6)))
    monkeypatch.setattr(mc, "separate_vocals", lambda stereo: np.zeros_like(stereo))
    assert mc.clean_clip(path)["status"] == "removed"
    loud = subprocess.run(["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                          capture_output=True, text=True).stderr
    assert "max_volume: -91" in loud


def test_bed_track_is_stable_per_clip(tmp_path):
    for name in ("b.mp3", "a.wav", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    first = mc.bed_track(Path("clip_1.mp4"), tmp_path)
    assert first in (tmp_path / "a.wav", tmp_path / "b.mp3")
    assert mc.bed_track(Path("clip_1.mp4"), tmp_path) == first
    assert mc.bed_track(Path("clip_1.mp4"), tmp_path / "nothing") is None


def test_bed_loops_to_cover_the_span():
    rate = 10
    audio = np.zeros((2, 80), dtype=np.float32)
    bed = np.ones((2, 15), dtype=np.float32)  # 1.5 s of free music for a 5 s stretch
    out = mc.add_bed(audio, bed, [(1.0, 6.0)], rate, gain=0.5, fade=0.3)
    assert np.all(out[:, :10] == 0) and np.all(out[:, 63:] == 0)  # nothing outside the stretch
    assert np.allclose(out[:, 14:56], 0.5)  # looped all the way, at the bed's gain


def test_bed_is_mixed_under_removed_music(tmp_path, monkeypatch):
    path = _clip(tmp_path)
    beds = tmp_path / "beds"
    beds.mkdir()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=2",
                    str(beds / "free.wav")], check=True)
    monkeypatch.setattr(mc, "MUSIC_BED_DIR", beds)
    monkeypatch.setattr(mc, "music_per_second", _music_then(np.zeros(6)))
    monkeypatch.setattr(mc, "separate_vocals", lambda stereo: np.zeros_like(stereo))
    assert mc.clean_clip(path)["status"] == "removed"
    loud = subprocess.run(["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                          capture_output=True, text=True).stderr
    assert "max_volume: -91" not in loud  # the free track is heard where the music was
