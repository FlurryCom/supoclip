from src.tools.caption_bench import assign, grade, spoken


def w(text, start, end, role, voice=None):
    out = {"text": text, "start": start, "end": end, "role": role}
    if voice:
        out["voice"] = voice
    return out


LINES = [
    [0.0, 2.0, "hey what happened", "fone", True],
    [2.5, 4.0, "oh my god", "xqc", False],
    [5.0, 7.0, "i think i lost her", "lucia", True],
    [8.0, 9.0, "(buzina)", "lixo", False],
]


def test_words_score_against_their_line():
    words = [w("hey", 0.1, 0.4, "game", "phone"), w("what", 0.5, 0.8, "game", "phone"),
             w("oh", 2.6, 2.9, "streamer"), w("god", 3.2, 3.6, "game", "npc"),
             w("lost", 5.5, 6.0, "game", "protagonist")]
    report = grade(words, LINES)
    assert report["right"] == 4 and report["wrong"] == 1
    assert report["confusion"] == {"xqc->npc": 1}
    assert report["missed_lines"] == [] and report["early_lines"] == []


def test_a_line_nobody_rendered_is_missed_and_an_early_word_is_flagged():
    report = grade([w("think", 4.2, 4.6, "game", "protagonist")], LINES)
    assert "hey what happened" in report["missed_lines"] and "oh my god" in report["missed_lines"]
    assert report["early_lines"] == ["i think i lost her"]  # 4.2 starts 0.8 s before the voice


def test_text_match_breaks_overlapping_lines():
    lines = [[0.0, 3.0, "get in the car", "npc", True], [0.0, 3.0, "no way bro", "xqc", False]]
    assert assign(w("car", 1.0, 1.4, "game"), lines) == 0
    assert assign(w("bro", 1.0, 1.4, "streamer"), lines) == 1
    assert spoken(w("car", 1.0, 1.4, "game")) == "npc"
