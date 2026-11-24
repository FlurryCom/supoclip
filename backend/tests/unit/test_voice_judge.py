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
    out = vj.judge(SAID, None, 16000, None, LINE, fallback, run=run, calls=calls)
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
