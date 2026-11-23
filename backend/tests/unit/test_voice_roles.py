import numpy as np

from src.media import voice_roles as vr


def word(start, end, text="hi"):
    return {"start": float(start), "end": float(end), "text": text}  # caption words: seconds on the clip


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


def test_single_voice_is_the_streamer():
    assert vr.assign_roles([0, 0, 0], {0: 9.0}, None, {0: 0.0}) == {0: "streamer"}


def test_mouth_picks_the_streamer():
    roles = vr.assign_roles([1, 0, 1, 0], {0: 5.0, 1: 20.0}, {0: 0.6, 1: 0.0}, {0: 0.0, 1: 0.9})
    assert roles[0] == "streamer"


def test_without_cam_the_longest_voice_is_the_streamer():
    roles = vr.assign_roles([1, 0, 1], {0: 5.0, 1: 20.0}, None, {0: 0.0, 1: 0.0})
    assert roles[1] == "streamer"


def test_voice_with_game_subtitles_is_game():
    roles = vr.assign_roles([0, 1, 0, 1], {0: 20.0, 1: 6.0}, None, {0: 0.0, 1: 0.8})
    assert roles == {0: "streamer", 1: "game"}


def test_voice_talking_back_is_friend():
    roles = vr.assign_roles([0, 1, 0, 1, 0], {0: 20.0, 1: 6.0}, None, {0: 0.0, 1: 0.0})
    assert roles[1] == "friend"


def test_unsure_voice_is_hidden():
    roles = vr.assign_roles([0, 0, 1], {0: 20.0, 1: 3.0}, None, {0: 0.0, 1: 0.3})
    assert roles[1] == "unsure"


def _video(tmp_path, name, vf):
    import subprocess
    path = tmp_path / name
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x305070:s=640x360:r=10:d=4",
                    "-vf", vf, "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_mouth_motion_rises_when_the_mouth_area_changes(tmp_path):
    # A box flickers inside the face's lower half only between 2 s and 3 s.
    path = _video(tmp_path, "m.mp4", "drawbox=x=110:y=140:w=40:h=20:color=white:t=fill:enable='between(t,2,3)*mod(n,2)'")
    face = (100.0, 100.0, 60.0, 80.0)
    motion = vr.mouth_motion(path, face, [0.5, 1.5, 2.4, 3.5])
    assert motion[2] > 5 * max(motion[0], motion[1], motion[3], 0.5)


def test_game_subtitles_on_sees_burned_lines(tmp_path):
    font = "/app/fonts/ArchivoBlack-Regular.ttf"
    path = _video(tmp_path, "s.mp4", f"drawtext=fontfile={font}:text='Go to the hangar now':fontsize=22:fontcolor=white:"
                  f"borderw=2:bordercolor=black:x=(w-text_w)/2:y=310:enable='between(t,1,2.5)'")
    assert vr.game_subtitles_on(path, [0.5, 1.5, 3.2]) == [False, True, False]


def _words_two_voices():
    # streamer 0-3 s, other 4-6 s, streamer 7-10 s, other 11-13 s
    return [word(0, 3, "a"), word(4, 6, "b"), word(7, 10, "c"), word(11, 13, "d")]


def _fake_pipeline(monkeypatch, subs):
    a, b = _unit([1, 0, 0]), _unit([0, 1, 0])
    vectors = iter([a, b, a, b])
    monkeypatch.setattr(vr, "_audio", lambda clip: np.zeros(16000 * 14, dtype=np.float32))
    monkeypatch.setattr(vr, "embed", lambda samples: next(vectors))
    monkeypatch.setattr(vr, "game_subtitles_on", lambda clip, times: [subs(t) for t in times])


def test_label_words_without_speech_keeps_everything():
    assert vr.label_words("clip.mp4", [], 1920, 1080) == []
    assert vr.label_words("clip.mp4", [word(0, 1)], 1920, 1080) == [{**word(0, 1), "role": "streamer"}]


def test_model_failure_falls_back_to_streamer(monkeypatch):
    monkeypatch.setattr(vr, "_audio", lambda clip: np.zeros(16000 * 14, dtype=np.float32))

    def broken(samples):
        raise RuntimeError("onnx down")

    monkeypatch.setattr(vr, "embed", broken)
    out = vr.label_words("clip.mp4", _words_two_voices(), 1920, 1080, face=None)
    assert [w["role"] for w in out] == ["streamer"] * 4


def test_hidden_game_words_are_dropped(monkeypatch):
    _fake_pipeline(monkeypatch, subs=lambda t: 4 <= t <= 6 or 11 <= t <= 13)  # game subtitles while voice b talks
    out = vr.label_words("clip.mp4", _words_two_voices(), 1920, 1080, face=None)
    assert [(w["text"], w["role"]) for w in out] == [("a", "streamer"), ("c", "streamer")]


def test_game_words_without_subtitles_on_screen_stay(monkeypatch):
    # b only gets game subtitles in its first stretch: still game (share 0.5), second stretch is shown
    _fake_pipeline(monkeypatch, subs=lambda t: 4 <= t <= 6)
    out = vr.label_words("clip.mp4", _words_two_voices(), 1920, 1080, face=None)
    assert [(w["text"], w["role"]) for w in out] == [("a", "streamer"), ("c", "streamer"), ("d", "game")]


def _scene(tmp_path, name, text_x):
    font = "/app/fonts/ArchivoBlack-Regular.ttf"
    return _video(tmp_path, name, f"drawtext=fontfile={font}:text='Cal where is Jay':fontsize=18:fontcolor=white:"
                  f"x={text_x}:y=318:enable='between(t,1,2.5)'")


def test_plain_white_centered_game_subtitle_is_seen(tmp_path):
    # GTA-style subtitles: plain white, no outline, centred near the bottom of a dark scene.
    path = _scene(tmp_path, "g.mp4", "(w-text_w)/2")
    assert vr.game_subtitles_on(path, [0.5, 1.5, 3.2]) == [False, True, False]


def test_corner_hud_text_is_not_a_subtitle(tmp_path):
    path = _scene(tmp_path, "h.mp4", "12")  # an objective line in the bottom-left corner
    assert vr.game_subtitles_on(path, [1.5]) == [False]


class _Body:
    def __init__(self, data):
        self.data = data

    def read(self, n=-1):
        out, self.data = self.data[:n] if n > 0 else self.data, self.data[n:] if n > 0 else b""
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_bad_download_leaves_no_model(monkeypatch, tmp_path):
    monkeypatch.setattr(vr, "MODEL_PATH", tmp_path / "voice" / "campplus.onnx")
    calls = []
    monkeypatch.setattr(vr.urllib.request, "urlopen", lambda url, timeout: calls.append(timeout) or _Body(b"not a model"))
    assert vr.ensure_model() is None
    assert calls == [30] and not vr.MODEL_PATH.exists() and not list(vr.MODEL_PATH.parent.glob("*.part*"))


def test_a_model_that_fails_to_load_is_removed(monkeypatch, tmp_path):
    model = tmp_path / "campplus.onnx"
    model.write_bytes(b"broken")
    monkeypatch.setattr(vr, "MODEL_PATH", model)
    monkeypatch.setattr(vr, "_session", None)
    import pytest
    with pytest.raises(Exception):
        vr._model()
    assert not model.exists()  # downloaded again next time instead of failing forever


def test_a_hud_line_always_on_screen_is_not_a_subtitle(monkeypatch):
    # A centred bright HUD bar reads as "subtitles on" everywhere, even while the streamer talks:
    # it must not make the other voice game (and drop it).
    _fake_pipeline(monkeypatch, subs=lambda t: True)
    out = vr.label_words("clip.mp4", _words_two_voices(), 1920, 1080, face=None)
    assert [(x["text"], x["role"]) for x in out] == [("a", "streamer"), ("b", "friend"), ("c", "streamer"), ("d", "friend")]


def test_a_streamer_voice_split_in_two_stays_the_streamer():
    # Shouting split the streamer's voice into groups 0 and 2; the mouth moves for both.
    roles = vr.assign_roles([0, 1, 2, 0, 2], {0: 10.0, 1: 4.0, 2: 6.0}, {0: 0.7, 1: 0.0, 2: 0.4}, {0: 0, 1: 0.9, 2: 0})
    assert roles == {0: "streamer", 1: "game", 2: "streamer"}


def test_frames_are_read_in_one_pass_whatever_the_order(tmp_path, monkeypatch):
    path = _scene(tmp_path, "o.mp4", "(w-text_w)/2")
    import cv2
    seeks = []
    real = cv2.VideoCapture

    class Counting:
        def __init__(self, *a):
            self.cap = real(*a)

        def set(self, prop, value):
            seeks.append(prop)
            return self.cap.set(prop, value)

        def __getattr__(self, name):
            return getattr(self.cap, name)

    monkeypatch.setattr(cv2, "VideoCapture", Counting)
    assert vr.game_subtitles_on(path, [3.2, 1.5, 0.5]) == [False, True, False]
    assert vr.mouth_motion(path, (100.0, 100.0, 60.0, 80.0), [2.4, 0.5]).shape == (2,)
    assert seeks == []  # no seeking: one forward read


def test_v3_keeps_subtitled_game_lines(monkeypatch):
    _fake_pipeline(monkeypatch, subs=lambda t: 4 <= t <= 6 or 11 <= t <= 13)
    out = vr.label_words("clip.mp4", _words_two_voices(), 1920, 1080, face=None, keep_game_lines=True)
    assert [(w["text"], w["role"]) for w in out] == [("a", "streamer"), ("b", "game"), ("c", "streamer"), ("d", "game")]


def test_v3_gives_unsure_voices_to_the_streamer(monkeypatch):
    # b talks once, at the end, with no subtitles: v2 calls it "unsure" and drops it; owner: in 3.0 it's the streamer
    _fake_pipeline(monkeypatch, subs=lambda t: False)
    words = [word(0, 3, "a"), word(4, 7, "c"), word(8, 10, "b")]
    a, b = _unit([1, 0, 0]), _unit([0, 1, 0])
    monkeypatch.setattr(vr, "embed", lambda samples, it=iter([a, a, b, a, a, b]): next(it))
    assert [w["text"] for w in vr.label_words("clip.mp4", words, 1920, 1080, face=None)] == ["a", "c"]
    out = vr.label_words("clip.mp4", words, 1920, 1080, face=None, keep_game_lines=True)
    assert [(w["text"], w["role"]) for w in out] == [("a", "streamer"), ("c", "streamer"), ("b", "streamer")]
