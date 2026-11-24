import numpy as np

from src.media import voice_judge as vj
from src.media import voice_roles as vr


def word(start, end, text):
    return {"text": text, "start": start, "end": end, "confidence": 1.0, "speaker": None}


def _words(*spec):
    return [word(a, b, t) for a, b, t in spec]


LINE = [(0.0, 2.0, ["tell", "her", "dre", "we", "can", "go", "on", "after"])]
SAID = _words((0.1, 0.4, "tell"), (0.4, 0.7, "the"), (0.7, 1.0, "driver"), (1.0, 1.3, "we"), (1.3, 1.6, "could"),
              (1.6, 1.9, "go"), (1.9, 2.2, "after"), (3.5, 3.8, "oh"), (3.8, 4.1, "my"), (4.1, 4.4, "god"))


def test_claude_sees_each_stretch_with_its_subtitle_loudness_and_mouth():
    asked = {}

    def run(text, images, schema, model, timeout):
        asked["text"] = text
        return {"lines": [{"first": 0, "last": 9, "speaker": "streamer", "text": ""}]}

    audio = np.zeros(16000 * 5, dtype=np.float32)
    audio[:16000 * 3] = 0.01
    audio[16000 * 3:] = 0.1  # the streamer's yell is 20 dB louder: +-10 around the median
    fallback = [{**w, "role": "streamer"} for w in SAID]
    vj.judge(SAID, audio, 16000, lambda times: [0.1, 1.9], LINE, fallback, run=run)
    text = asked["text"]
    assert 'GTA subtitle: "tell her dre we can go on after"' in text
    assert "0:tell 1:the 2:driver" in text and "7:oh 8:my 9:god" in text
    assert "loud -10.0 dB" in text and "loud +10.0 dB" in text and "mouth 1.90" in text
    assert "from 0 to 9" in text


def test_a_game_line_gets_the_subtitle_s_words_over_the_same_time():
    answer = {"lines": [{"first": 0, "last": 6, "speaker": "game", "text": "Tell her, Dre. We can go on after"},
                        {"first": 7, "last": 9, "speaker": "streamer", "text": ""}]}
    fallback = [{**w, "role": "streamer"} for w in SAID]
    out = vj.apply_verdict(SAID, answer, fallback, subtitled=set(range(7)))
    assert [w["text"] for w in out] == ["Tell", "her,", "Dre.", "We", "can", "go", "on", "after", "oh", "my", "god"]
    assert [w["role"] for w in out] == ["game"] * 8 + ["streamer"] * 3
    assert out[0]["start"] == 0.1 and abs(out[7]["end"] - 2.2) < 1e-9
    assert all(a["end"] <= b["start"] + 1e-9 for a, b in zip(out, out[1:]))


def test_claude_can_t_rewrite_what_no_subtitle_showed():
    answer = {"lines": [{"first": 7, "last": 9, "speaker": "game", "text": "Oh, my God!"}]}
    fallback = [{**w, "role": "streamer"} for w in SAID]
    out = vj.apply_verdict(SAID, answer, fallback, subtitled=set(range(7)))
    assert [w["text"] for w in out] == [w["text"] for w in SAID]
    assert [w["role"] for w in out] == ["streamer"] * 7 + ["game"] * 3


def test_words_claude_skipped_or_bad_ranges_keep_the_fallback_role():
    answer = {"lines": [{"first": 5, "last": 99, "speaker": "game", "text": ""},
                        {"first": 0, "last": 1, "speaker": "narrator", "text": ""},
                        {"first": 2, "last": 3, "speaker": "game", "text": ""}]}
    fallback = [{**w, "role": "streamer"} for w in SAID]
    out = vj.apply_verdict(SAID, answer, fallback, subtitled=set())
    assert [w["role"] for w in out] == ["streamer", "streamer", "game", "game"] + ["streamer"] * 6


def test_v3_falls_back_to_the_subtitle_match_when_claude_fails(monkeypatch):
    monkeypatch.setattr(vr, "subtitle_tokens", lambda clip, times: [
        vr._token_list("So, you're saying you ran?") if 0 <= t <= 2 else [] for t in times])
    monkeypatch.setattr(vr, "_audio", lambda clip: np.zeros(16000 * 4, dtype=np.float32))

    def broken(*args, **kwargs):
        raise RuntimeError("claude CLI timed out")
    monkeypatch.setattr("src.claude_cli.run_cli_images", broken)
    words = _words((0.2, 0.5, "So,"), (0.5, 0.8, "you're"), (0.8, 1.2, "saying"), (1.2, 1.4, "you"),
                   (1.4, 1.8, "ran?"), (3.0, 3.4, "bro"))
    out = vr.label_words("clip.mp4", words, 1920, 1080, keep_game_lines=True)
    assert [w["role"] for w in out] == ["game"] * 5 + ["streamer"]


def test_v3_uses_claude_s_verdict(monkeypatch):
    monkeypatch.setattr(vr, "subtitle_tokens", lambda clip, times: [[] for _ in times])
    monkeypatch.setattr(vr, "_audio", lambda clip: np.zeros(16000 * 4, dtype=np.float32))
    monkeypatch.setattr("src.claude_cli.run_cli_images", lambda *a, **k: {
        "lines": [{"first": 0, "last": 1, "speaker": "game", "text": ""}]})
    out = vr.label_words("clip.mp4", _words((0.2, 0.5, "Hey,"), (0.5, 0.8, "Lucia")), 1920, 1080,
                         keep_game_lines=True)
    assert [w["role"] for w in out] == ["game", "game"]


def test_a_subtitle_pinned_on_the_streamer_s_yelling_is_ignored():
    # Claude matched the game's line to words that sound nothing like it: that call is wrong
    said = _words((0.1, 0.4, "Oh"), (0.4, 0.7, "my"), (0.7, 1.0, "god."), (1.0, 1.3, "Hang"), (1.3, 1.6, "up"),
                  (1.6, 1.9, "the"), (1.9, 2.2, "phone"), (2.2, 2.5, "you"), (2.5, 2.8, "bum"))
    answer = {"lines": [{"first": 0, "last": 8, "speaker": "game", "text": "Hey, what happened? I dunno, I think I lost her."}]}
    fallback = [{**w, "role": "streamer"} for w in said]
    out = vj.apply_verdict(said, answer, fallback, subtitled=set(range(9)))
    assert [w["text"] for w in out] == [w["text"] for w in said]
    assert [w["role"] for w in out] == ["streamer"] * 9


def test_claude_hears_who_is_on_the_phone_and_tells_the_voices_apart():
    asked = {}
    calls = [(0.0, 2.5, "Jason")]

    def run(text, images, schema, model, timeout):
        asked["text"] = text
        return {"lines": [{"first": 0, "last": 3, "speaker": "phone", "text": ""},
                          {"first": 4, "last": 6, "speaker": "protagonist", "text": ""},
                          {"first": 7, "last": 9, "speaker": "streamer", "text": ""}]}

    fallback = [{**w, "role": "streamer"} for w in SAID]
    out = vj.judge(SAID, None, 16000, None, [], fallback, run=run, calls=calls)  # no subtitle: Claude's split
    assert "call: Jason" in asked["text"] and "protagonist" in asked["text"]
    assert [w.get("voice") for w in out] == ["phone"] * 4 + ["protagonist"] * 3 + [None] * 3
    assert [w.get("caller") for w in out[:4]] == ["Jason"] * 4 and "caller" not in out[4]
    assert [w["role"] for w in out] == ["game"] * 7 + ["streamer"] * 3


def test_a_phone_voice_without_a_call_card_is_just_a_character():
    answer = {"lines": [{"first": 7, "last": 9, "speaker": "phone", "text": ""}]}
    fallback = [{**w, "role": "streamer"} for w in SAID]
    out = vj.apply_verdict(SAID, answer, fallback, subtitled=set(), calls=[(0.0, 2.5, "Jason")])
    assert [w.get("voice") for w in out[7:]] == ["npc"] * 3 and "caller" not in out[7]


def test_during_a_call_a_game_line_that_isn_t_the_protagonist_comes_from_the_phone():
    # owner's rule: while the call card is up, the other voice is the caller, whatever Claude guessed
    answer = {"lines": [{"first": 0, "last": 3, "speaker": "npc", "text": ""},
                        {"first": 4, "last": 6, "speaker": "protagonist", "text": ""},
                        {"first": 7, "last": 9, "speaker": "npc", "text": ""}]}
    fallback = [{**w, "role": "streamer"} for w in SAID]
    out = vj.apply_verdict(SAID, answer, fallback, subtitled=set(), calls=[(0.0, 1.2, "Jason")])
    assert [w.get("voice") for w in out] == ["phone"] * 4 + ["protagonist"] * 3 + ["npc"] * 3
    assert out[0]["caller"] == "Jason"


def test_v3_without_claude_game_lines_in_a_call_still_come_from_the_phone(monkeypatch):
    monkeypatch.setattr(vr, "subtitle_tokens", lambda clip, times: [
        vr._token_list("So, you're saying you ran?") if 0 <= t <= 2 else [] for t in times])
    monkeypatch.setattr(vr, "_audio", lambda clip: np.zeros(16000 * 4, dtype=np.float32))
    monkeypatch.setattr(vr, "_calls", lambda clip, words: [(0.0, 2.0, "Jason")])

    def broken(*args, **kwargs):
        raise RuntimeError("claude CLI timed out")
    monkeypatch.setattr("src.claude_cli.run_cli_images", broken)
    words = _words((0.2, 0.5, "So,"), (0.5, 0.8, "you're"), (0.8, 1.2, "saying"), (1.2, 1.4, "you"),
                   (1.4, 1.8, "ran?"), (3.0, 3.4, "bro"))
    out = vr.label_words("clip.mp4", words, 1920, 1080, keep_game_lines=True)
    assert [w.get("voice") for w in out] == ["phone"] * 5 + [None] and out[0]["caller"] == "Jason"


def test_every_gta_subtitle_is_a_game_line_even_one_whisper_never_heard():
    # xQc yells over Jason's call: Whisper only has him, but GTA wrote both lines of the call
    said = _words((41.9, 42.4, "Hang"), (42.4, 42.9, "up."), (45.4, 45.8, "you"), (45.8, 46.3, "bum"))
    subs = [(41.5, 42.5, ["hey", "what", "happened"]), (43.0, 44.5, ["i", "dunno", "i", "think", "i", "lost", "her"])]
    roles = [{**w, "role": "streamer"} for w in said]
    verdicts = [{"id": 0, "speaker": "npc", "text": "Hey, what happened?"},
                {"id": 1, "speaker": "protagonist", "text": "I dunno, I think I lost her."}]
    out = vj.with_subtitles(roles, subs, verdicts, calls=[(39.5, 48.5, "Jason")])
    game = [w for w in out if w["role"] == "game"]
    assert [w["text"] for w in game] == "Hey, what happened? I dunno, I think I lost her.".split()
    assert [w["voice"] for w in game] == ["phone"] * 3 + ["protagonist"] * 7 and game[0]["caller"] == "Jason"
    assert [w["text"] for w in out if w["role"] == "streamer"] == ["Hang", "up.", "you", "bum"]
    assert all(a["start"] <= b["start"] for a, b in zip(out, out[1:]) if a["role"] == b["role"])


def test_a_subtitle_heard_mixed_into_the_streamer_s_talk_is_the_npc_s():
    # "My collection then I get up is all my music shit": the NPC's "...this music shit" under xQc's talk
    said = _words((15.0, 15.5, "My"), (15.5, 16.0, "collection"), (16.0, 16.5, "then"), (16.5, 17.0, "I"),
                  (17.0, 17.5, "get"), (17.5, 18.0, "up"), (18.0, 18.5, "is"), (18.5, 19.0, "all"),
                  (19.0, 19.3, "my"), (19.3, 19.6, "music"), (19.6, 19.9, "shit"))
    subs = [(16.0, 19.5, "i'm done i'm straight off this music shit".split())]
    out = vj.with_subtitles([{**w, "role": "streamer"} for w in said], subs, [{"id": 0, "speaker": "npc", "npc": 2,
                            "text": "I'm done, I'm straight off this music shit."}])
    game = [w for w in out if w["role"] == "game"]
    assert " ".join(w["text"] for w in game) == "I'm done, I'm straight off this music shit."
    assert game[0]["npc"] == 2 and game[-1]["end"] == 19.9


def test_an_unheard_subtitle_waits_for_its_voice():
    # GTA draws "hey what happened" at 41.0 s; the voice only starts at 41.9 s
    subs = [(41.0, 41.7, ["hey", "what", "happened"])]

    def timer(text, a, b):
        words = text.split()
        return [(w, 41.9 + k * 0.3, 42.2 + k * 0.3) for k, w in enumerate(words)]

    out = vj.with_subtitles([], subs, [{"id": 0, "speaker": "npc", "text": "Hey, what happened?"}], timer=timer)
    assert [w["text"] for w in out] == ["Hey,", "what", "happened?"]
    assert out[0]["start"] == 41.9 and abs(out[-1]["end"] - 42.8) < 1e-6


def test_a_heard_subtitle_keeps_the_transcript_s_times():
    said = _words((10.0, 10.4, "so"), (10.4, 10.8, "you're"), (10.8, 11.2, "saying"), (11.2, 11.6, "you"),
                  (11.6, 12.0, "ran"))
    subs = [(9.7, 11.5, ["so", "you're", "saying", "you", "ran"])]
    out = vj.with_subtitles([{**w, "role": "game"} for w in said], subs, [],
                            timer=lambda text, a, b: [(t, 99.0, 99.5) for t in text.split()])
    assert out[0]["start"] == 10.0 and out[-1]["end"] == 12.0  # the timer is for unheard lines only


def test_a_low_cam_sync_flips_a_streamer_verdict_to_the_game():
    # the webcam face is clearly NOT talking (cam sync 0.1): the words can't be the streamer's
    answer = {"lines": [{"first": 0, "last": 9, "speaker": "streamer", "text": ""}]}
    fallback = [{**w, "role": "streamer"} for w in SAID]
    out = vj.apply_verdict(SAID, answer, fallback, subtitled=set(), low=set(range(7)))
    assert [w["role"] for w in out] == ["game"] * 7 + ["streamer"] * 3


def test_claude_runs_twice_and_disagreement_keeps_the_fallback():
    answers = iter([
        {"lines": [{"first": 0, "last": 6, "speaker": "npc", "text": ""},
                   {"first": 7, "last": 9, "speaker": "npc", "text": ""}]},
        {"lines": [{"first": 0, "last": 6, "speaker": "npc", "text": ""},
                   {"first": 7, "last": 9, "speaker": "streamer", "text": ""}]},
    ])
    fallback = [{**w, "role": "streamer"} for w in SAID]
    out = vj.judge(SAID, None, 16000, None, [], fallback, run=lambda *a, **k: next(answers))
    assert [w["role"] for w in out[:7]] == ["game"] * 7  # both passes agreed
    assert [w["role"] for w in out[7:]] == ["streamer"] * 3  # disputed: the fallback stands


def test_the_prompt_tells_claude_how_surely_the_cam_face_talks():
    asked = {}

    def run(text, images, schema, model, timeout):
        asked.setdefault("text", text)
        return {"lines": [{"first": 0, "last": 9, "speaker": "streamer", "text": ""}]}

    fallback = [{**w, "role": "streamer"} for w in SAID]
    vj.judge(SAID, None, 16000, None, [], fallback, run=run,
             speak_at=lambda spans: [0.95 for _ in spans])
    assert "cam 0.95" in asked["text"]


def test_a_subtitle_never_swallows_words_that_sound_nothing_like_it():
    # xQc yells "oh my god" while S0 shows; his words must not be replaced by the subtitle's text
    said = _words((42.0, 42.4, "oh"), (42.4, 42.8, "my"), (42.8, 43.2, "god"))
    subs = [(41.5, 43.0, ["i", "dunno", "i", "think", "i", "lost", "her"])]
    out = vj.with_subtitles([{**w, "role": "game"} for w in said], subs, [])
    texts = [w["text"] for w in out]
    assert "oh" in texts and "my" in texts and "god" in texts  # kept, not absorbed
    assert "dunno" in texts  # the unheard line still renders on its own


def test_a_subtitle_claude_calls_music_is_not_captioned():
    # GTA subtitles club songs too; lyrics must not become an NPC line
    said = _words((35.5, 36.0, "get"), (36.0, 36.5, "in"))
    subs = [(35.5, 36.5, ["get", "in", "bae", "luxe's"])]
    out = vj.with_subtitles([{**w, "role": "game"} for w in said], subs,
                            [{"id": 0, "speaker": "music", "text": "Get in, Bae Luxe's"}])
    assert out == []
