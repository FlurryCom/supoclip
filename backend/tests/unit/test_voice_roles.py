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
    return _video(tmp_path, name, f"drawtext=fontfile={font}:text='Cal where is Jay':fontsize=9:fontcolor=white:"
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


def _bottom_text(size, thickness):
    import cv2
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    cv2.putText(frame, "Give me your cash now", (700, 990), cv2.FONT_HERSHEY_SIMPLEX, size, (255, 255, 255), thickness)
    return frame


def test_plain_subtitle_is_the_games_thin_line_not_a_big_burned_caption():
    assert vr.plain_subtitle_on(_bottom_text(0.7, 2))  # GTA subtitle: ~1.5% of the frame height
    assert not vr.plain_subtitle_on(_bottom_text(2.0, 6))  # an editor's caption already burned in: ~5%


def _fake_subtitles(monkeypatch, shown):
    """``shown``: (from, to, text) of the game's subtitle on screen."""
    monkeypatch.setattr(vr, "subtitle_tokens", lambda clip, times: [
        sum([vr._token_list(text) for a, b, text in shown if a <= t <= b], []) for t in times])


def test_v3_npc_words_are_the_ones_written_in_the_games_subtitle(monkeypatch):
    _fake_subtitles(monkeypatch, [(4.0, 6.5, "So, you're saying you ran?")])
    words = [word(0, 1, "Whoa,"), word(1, 2, "dude"), word(4.2, 4.5, "So,"), word(4.5, 4.8, "you're"),
             word(4.8, 5.3, "saying"), word(5.3, 5.5, "you"), word(5.5, 6.0, "ran?"), word(8, 9, "crazy")]
    out = vr.label_words("clip.mp4", words, 1920, 1080, keep_game_lines=True)
    assert [w["role"] for w in out] == ["streamer", "streamer", "game", "game", "game", "game", "game", "streamer"]


def test_v3_streamer_talking_over_a_subtitle_stays_the_streamer_and_the_line_shows_too(monkeypatch):
    # GTA shows the NPC's line while the streamer yells something else (Whisper only heard the streamer):
    # the streamer's words stay his, and the subtitle still becomes the NPC's line (owner's rule)
    _fake_subtitles(monkeypatch, [(0.0, 3.0, "Get in the car and go get her.")])
    words = [word(0.2, 0.6, "Oh"), word(0.6, 1.0, "my"), word(1.0, 1.5, "god"), word(1.5, 1.8, "the"),
             word(1.8, 2.2, "button")]
    out = vr.label_words("clip.mp4", words, 1920, 1080, keep_game_lines=True)
    streamer = [w["text"] for w in out if w["role"] == "streamer"]
    game = [w for w in out if w["role"] == "game"]
    assert streamer == ["Oh", "my", "god", "the", "button"]
    assert [w["text"] for w in game] == "get in the car and go get her".split() and game[0]["start"] == 0.0
    assert all(w["voice"] == "npc" for w in game)


def test_v3_ocr_trouble_leaves_everything_to_the_streamer(monkeypatch):
    def broken(clip, times):
        raise RuntimeError("no tesseract")
    monkeypatch.setattr(vr, "subtitle_tokens", broken)
    out = vr.label_words("clip.mp4", [word(0, 1, "hey")], 1920, 1080, keep_game_lines=True)
    assert out == [{**word(0, 1, "hey"), "role": "streamer"}]


def test_subtitle_text_reads_the_games_line():
    import cv2
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    cv2.putText(frame, "Get in the car", (760, 990), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    assert "car" in vr._tokens(vr.subtitle_text(frame))
    assert vr.subtitle_text(np.zeros((1080, 1920, 3), dtype=np.uint8)) == ""


def test_v3_a_word_the_ocr_missed_inside_an_npc_line_stays_npc(monkeypatch):
    _fake_subtitles(monkeypatch, [(0.0, 3.0, "I don't see you... come out to the parking lot.")])
    words = [word(0.2, 0.5, "see"), word(0.5, 0.8, "you"), word(0.8, 1.1, "come"), word(1.1, 1.4, "into"),
             word(1.4, 1.6, "the"), word(1.6, 2.0, "parking"), word(2.0, 2.3, "lot")]
    out = vr.label_words("clip.mp4", words, 1920, 1080, keep_game_lines=True)
    assert [w["text"] for w in out] == "i don't see you come out to the parking lot".split()  # the subtitle's words
    assert all(w["role"] == "game" for w in out) and out[0]["start"] == 0.2 and out[-1]["end"] == 2.3


def test_v3_a_misheard_npc_line_is_the_npc_from_end_to_end(monkeypatch):
    # Whisper hears "Tell her, Dre. We can go on after" as "tell the driver we could go after"
    _fake_subtitles(monkeypatch, [(0.0, 3.0, "Right? Tell her, Dre. We can go on after, right?")])
    said = "Right tell the driver we could go after right".split()
    words = [word(0.1 + 0.3 * i, 0.4 + 0.3 * i, text) for i, text in enumerate(said)]
    out = vr.label_words("clip.mp4", words + [word(4.0, 4.5, "bro")], 1920, 1080, keep_game_lines=True)
    assert [w["text"] for w in out] == "right tell her dre we can go on after right".split() + ["bro"]
    assert [w["role"] for w in out] == ["game"] * 10 + ["streamer"]


def test_v3_the_npc_line_takes_its_unmatched_tail_but_not_the_streamer_after_it(monkeypatch):
    _fake_subtitles(monkeypatch, [(0.0, 1.8, "Girl, you drivin', let's go.")])
    said = [word(0.1, 0.3, "Girl,"), word(0.3, 0.5, "you"), word(0.5, 0.8, "try"), word(0.8, 1.1, "hang"),
            word(1.1, 1.4, "up."), word(2.6, 2.8, "Oh"), word(2.8, 3.0, "my"), word(3.0, 3.3, "god.")]
    out = vr.label_words("clip.mp4", said, 1920, 1080, keep_game_lines=True)
    assert [w["role"] for w in out] == ["game"] * 5 + ["streamer"] * 3


def test_v3_the_line_s_last_words_said_just_after_the_subtitle_went_away_are_still_the_npc(monkeypatch):
    # sampled 3x a second, the subtitle's last sighting is up to 1/3 s before it really goes
    _fake_subtitles(monkeypatch, [(49.7, 51.3, "Yeah, I ran, I mean...")])
    said = [word(49.1, 49.4, "Yeah,"), word(49.5, 49.6, "I"), word(49.6, 50.0, "ran."), word(51.5, 51.8, "I"),
            word(51.9, 52.2, "mean..."), word(53.5, 53.8, "dude")]
    out = vr.label_words("clip.mp4", said, 1920, 1080, keep_game_lines=True)
    assert [w["role"] for w in out] == ["game"] * 5 + ["streamer"]


def test_ocr_noise_is_not_a_subtitle():
    lines = [(4.0, 4.0, ["2", "s", "t"]), (8.33, 8.33, ["rrkrkkkk", "rrr"]), (9.0, 9.67, ["rerkkkk", "rrr"]),
             (10.0, 12.0, ["get", "in", "the", "car"])]
    assert vr.readable(lines) == [(10.0, 12.0, ["get", "in", "the", "car"])]


def test_v3_the_cam_sync_reaches_the_judge(monkeypatch):
    monkeypatch.setattr(vr, "subtitle_tokens", lambda clip, times: [[] for _ in times])
    monkeypatch.setattr(vr, "_audio", lambda clip: np.zeros(16000 * 4, dtype=np.float32))
    monkeypatch.setattr(vr, "mouth_motion", lambda clip, face, ts: [1.0 for _ in ts])
    monkeypatch.setattr("src.media.asd.speaking_scores", lambda clip, face, spans: [0.1 for _ in spans])
    monkeypatch.setattr("src.claude_cli.run_cli_images", lambda *a, **k: {
        "lines": [{"first": 0, "last": 1, "speaker": "streamer", "text": ""}]})
    out = vr.label_words("clip.mp4", [word(0.2, 0.5, "Hey,"), word(0.5, 0.8, "Lucia")], 1920, 1080,
                         face=(0, 0, 50, 50), keep_game_lines=True)
    assert [w["role"] for w in out] == ["game", "game"]  # cam 0.1: not the streamer, whatever Claude said
