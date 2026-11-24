from pathlib import Path

from src.media.captions import build_assemblyai_ass_subtitles


def w(text, start, end, role):
    return {"text": text, "start": start, "end": end, "role": role}


def _ass(tmp_path, words):
    out = tmp_path / "c.ass"
    assert build_assemblyai_ass_subtitles(
        tmp_path / "missing.mp4", 0.0, 10.0, 1080, 1920, out, caption_template="catclips", caption_words=words,
    )
    return [line for line in out.read_text().splitlines() if line.startswith("Dialogue:")]


def test_role_change_breaks_the_caption_block(tmp_path):
    lines = _ass(tmp_path, [
        w("go", 0.0, 0.4, "streamer"), w("now", 0.4, 0.8, "streamer"),
        w("hands", 1.0, 1.4, "game"), w("up", 1.4, 1.8, "game"),
        w("bro", 2.0, 2.4, "friend"), w("run", 2.4, 2.8, "friend"),
    ])
    game = [line for line in lines if "HANDS" in line]
    friend = [line for line in lines if "BRO" in line]
    streamer = [line for line in lines if "GO" in line]
    assert len(game) == 1 and "\\fscx75" in game[0] and "\\c&HFFFFFF&" in game[0] and "GO" not in game[0]
    assert len(friend) == 1 and "\\c&HFFCB7C&" in friend[0] and "\\fscx75" not in friend[0]
    assert streamer and all("HANDS" not in line and "BRO" not in line for line in streamer)


def test_words_without_roles_render_as_before(tmp_path):
    lines = _ass(tmp_path, [w("go", 0.0, 0.4, None), w("now", 0.4, 0.8, None)])
    assert lines and not any("\\fscx75" in line for line in lines)


def test_a_gap_left_by_hidden_lines_ends_the_caption(tmp_path):
    # "sir" ... (hidden game lines) ... "just": the caption must not hang on screen through the gap.
    lines = _ass(tmp_path, [w("right", 0.0, 0.4, "streamer"), w("sir", 0.4, 0.8, "streamer"),
                            w("just", 15.0, 15.4, "streamer"), w("go", 15.4, 15.8, "streamer")])
    for line in lines:
        start, end = line.split(",")[1:3]
        to_s = lambda t: int(t.split(":")[0]) * 3600 + int(t.split(":")[1]) * 60 + float(t.split(":")[2])
        assert to_s(end) - to_s(start) < 3.0, line


def test_a_cut_marked_on_a_word_starts_a_new_caption(tmp_path):
    # Crossfaded cut between "two" and "three": no pause on the clip timeline, but a new passage.
    words = [w("one", 0.0, 0.3, "streamer"), w("two", 0.3, 0.6, "streamer"),
             {**w("three", 0.6, 0.9, "streamer"), "break_before": True}, w("four", 0.9, 1.2, "streamer")]
    lines = _ass(tmp_path, words)
    assert not any("TWO" in line and "THREE" in line for line in lines)


def _v3(tmp_path, words):
    out = tmp_path / "v3.ass"
    assert build_assemblyai_ass_subtitles(
        tmp_path / "missing.mp4", 0.0, 10.0, 1080, 1920, out, caption_template="catclips3", caption_words=words,
    )
    return [line for line in out.read_text().splitlines() if line.startswith("Dialogue:")]


def _at(line):
    import re
    m = re.search(r"\\(?:pos|move)\((-?\d+),(-?\d+)", line)
    return int(m.group(1)), int(m.group(2))


def test_v3_each_word_balloons_in_on_its_own_two_per_line(tmp_path):
    # owner's reference: a word shows up as it's said, growing fast like a balloon; newest lemon yellow,
    # the ones before white; two words a line
    lines = _v3(tmp_path, [w("your", 0.0, 0.3, "streamer"), w("cash", 0.3, 0.6, "streamer"),
                           w("now", 0.6, 0.9, "streamer"), w("bro", 0.9, 1.2, "streamer")])
    text = [line for line in lines if line.startswith("Dialogue: 1,")]
    cash = [line for line in text if line.endswith("}CASH")]
    assert cash[0].startswith("Dialogue: 1,0:00:00.30")  # not on screen before it's said
    assert "\\t(0,70," in cash[0] and "F0F5" in cash[0].upper()  # balloons in, lemon yellow (#F5F000)
    assert "\\t(" not in cash[1] and "\\c&H00FFFFFF&" in cash[1]  # once the next word comes: white, still
    your, now = _at(text[0]), _at(next(line for line in text if line.endswith("}NOW")))
    assert _at(cash[0])[1] == your[1] < now[1]  # YOUR CASH on top, NOW BRO below
    assert all("Montserrat Black" in line for line in text)
    assert any(line.startswith("Dialogue: 0,") and "\\blur" in line for line in lines)  # the soft dark halo


def test_v3_npc_words_balloon_in_over_the_npc(tmp_path):
    path = [(1.0, 2.0, 500, 1300, 500, 1300)]
    lines = _v3(tmp_path, [{**w("give", 1.0, 1.4, "game"), "npc_path": path},
                           {**w("me", 1.4, 2.0, "game"), "npc_path": path}])
    text = [line for line in lines if line.startswith("Dialogue: 1,")]
    give, me = next(line for line in text if line.endswith("}GIVE")), next(line for line in text if line.endswith("}ME"))
    assert me.startswith("Dialogue: 1,0:00:01.40")
    (gx, gy), (mx, my) = _at(give), _at(me)
    assert gy == my < 1300 and gx < 500 < mx  # one line, sitting on the anchor above the head
    arrow = next(line for line in text if "\\p1" in line)
    assert _at(arrow) == (500, 1300) and "\\c&H00C4C4C4&" in give  # the arrow's tip on the head; an NPC is grey


def test_v3_streamer_block_and_its_arrow_clear_the_title_then_sit_on_the_seam(tmp_path):
    from src.media.catclips3 import GAME_Y, TITLE_BOTTOM, TITLE_OUT
    from src.media.npc_captions import streamer_path
    words = [{**w(t, s, s + 0.3, "streamer"), "seat_path": streamer_path()}
             for t, s in (("oh", 0.0), ("my", 0.3), ("god", 0.6), ("bro", 0.9), ("go", 5.0), ("now", 5.3))]
    lines = _v3(tmp_path, words)
    text = [line for line in lines if line.startswith("Dialogue: 1,")]
    oh = next(line for line in text if line.endswith("}OH"))
    arrow = next(line for line in text if "\\p1" in line)
    assert _at(oh)[1] - 0.4 * 100 >= TITLE_BOTTOM + 20  # the top line's letters clear the title badge
    assert _at(arrow)[1] - 24 >= TITLE_BOTTOM + 10 and _at(arrow)[1] < _at(oh)[1]  # the arrow, above the line
    assert "\\c&H0000F0F5&" in arrow  # yellow, pointing up at the cam
    go = next(line for line in text if line.endswith("}GO"))
    assert abs(_at(go)[1] - GAME_Y) <= 45  # on the seam once the title is gone (one row: centred on it)


def test_v3_streamer_lines_sit_tight(tmp_path):
    lines = _v3(tmp_path, [w(t, i * 0.3, i * 0.3 + 0.3, "streamer") for i, t in enumerate(["oh", "my", "god", "bro"])])
    text = [line for line in lines if line.startswith("Dialogue: 1,")]
    oh, god = (_at(next(line for line in text if line.endswith(f"}}{t}")))[1] for t in ("OH", "GOD"))
    assert god - oh <= 82  # one line under the other, the 100 px caps nearly touching


def test_v3_a_phone_line_is_green_with_the_caller_s_name(tmp_path):
    path = [(1.0, 2.0, 540, 1139, 540, 1139)]
    lines = _v3(tmp_path, [{**w(t, s, s + 0.4, "game"), "npc_path": path, "voice": "phone", "caller": "Jason"}
                           for t, s in (("where", 1.0), ("are", 1.4))])
    label = next(line for line in lines if "JASON" in line)
    words = [line for line in lines if line.startswith("Dialogue: 1,") and line.endswith(("}WHERE", "}ARE"))]
    assert "\\p1" in label and "\\c&H005FDC3D&" in label and all("\\c&H005FDC3D&" in line for line in words)
    assert _at(label)[1] < min(_at(line)[1] for line in words)  # the label over the line


def test_v3_two_npcs_and_the_streamer_talking_over_a_line_each_get_their_own_caption(tmp_path):
    a, b = [(15.0, 20.0, 300, 1200, 300, 1200)], [(20.0, 22.0, 800, 1200, 800, 1200)]
    words = [{**w("done", 15.0, 17.0, "game"), "voice": "npc", "npc": 1, "npc_path": a},
             {**w("shit", 17.0, 20.0, "game"), "voice": "npc", "npc": 1, "npc_path": a},
             w("collection", 15.5, 16.0, "streamer"),  # xQc, said while the NPC's line shows
             {**w("slow", 20.0, 21.0, "game"), "voice": "npc", "npc": 2, "npc_path": b},
             {**w("down", 21.0, 22.0, "game"), "voice": "npc", "npc": 2, "npc_path": b}]
    lines = [line for line in _v3(tmp_path, words) if line.startswith("Dialogue: 1,")]
    done = next(line for line in lines if line.endswith("}DONE"))
    slow = next(line for line in lines if line.endswith("}SLOW"))
    xqc = next(line for line in lines if line.endswith("}COLLECTION"))
    assert _at(done)[0] < 540 < _at(slow)[0]  # each NPC over its own head
    assert xqc.startswith("Dialogue: 1,0:00:15.50") and done.startswith("Dialogue: 1,0:00:15.00")
