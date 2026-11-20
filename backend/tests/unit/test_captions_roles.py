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
