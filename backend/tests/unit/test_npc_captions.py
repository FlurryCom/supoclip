from src.media.catclips3 import GAME_Y, TITLE_BOTTOM
from src.media.npc_captions import EDGE_X, FIXED, annotate_npc_words, head_track, line_path, segments_between

CROP = (554, 0, 812, 918)  # game cut of a 1920x1080 source -> 1080x1220 at y=700
CAM_FACE = (150.0, 500.0, 80.0, 110.0)  # webcam face, outside the crop


def npc(x, y=300.0, size=120.0):
    return (x - size / 2, y, size, size * 1.2)  # face box centred on x


def samples(*frames):
    return [(t, faces) for t, faces in frames]


def test_npc_line_sits_above_the_visible_face():
    # an NPC off to the side (the middle of the crop is the protagonist's spot)
    track = head_track(samples((0.0, [npc(800)]), (0.5, [npc(800)]), (1.0, [npc(800)])), None, CROP)
    path = line_path(0.0, 1.0, track)
    x, y = path[0][2], path[0][3]
    assert abs(x - round((800 - 554) * 1080 / 812)) <= 2
    assert TITLE_BOTTOM + 100 <= y < GAME_Y + (300 - 0) * 1080 / 812  # above the head, inside the game area


def test_npc_before_appearing_waits_on_its_side():
    frames = [(t / 2, [] if t < 4 else [npc(700)]) for t in range(8)]  # appears at 2 s, left of centre
    path = line_path(0.0, 3.5, head_track(samples(*frames), None, CROP))
    assert path[0][0] == 0.0 and path[0][2] == EDGE_X  # waits at the left edge
    assert path[-1][4] > EDGE_X + 100  # then over the head


def test_npc_follows_a_moving_face():
    frames = [(t / 2, [npc(700 + 100 * t)]) for t in range(6)]
    path = line_path(0.0, 2.5, head_track(samples(*frames), None, CROP))
    xs = [seg[2] for seg in path] + [path[-1][4]]
    assert xs == sorted(xs) and xs[-1] > xs[0]


def test_npc_without_faces_uses_the_fixed_spot():
    path = line_path(1.0, 2.0, head_track(samples((0.0, []), (1.5, [])), None, CROP))
    assert path == [(1.0, 2.0, *FIXED, *FIXED)]


def test_npc_ignores_the_webcam_face():
    cam_inside = (900.0, 600.0, 80.0, 100.0)  # a webcam face that falls inside the crop
    track = head_track(samples((0.0, [cam_inside]), (1.0, [cam_inside])), cam_inside, CROP)
    assert line_path(0.0, 1.0, track) == [(0.0, 1.0, *FIXED, *FIXED)]


def test_npc_stays_inside_the_game_area():
    track = head_track(samples((0.0, [npc(959, y=0.0)])), None, CROP)  # head at the very top of the crop
    assert line_path(0.0, 1.0, track)[0][3] >= TITLE_BOTTOM + 100


def test_segments_between_cuts_a_path_at_chunk_bounds():
    path = [(0.0, 2.0, 100, 1000, 300, 1000)]
    assert segments_between(path, 1.0, 2.0) == [(1.0, 2.0, 200, 1000, 300, 1000)]


def test_annotate_npc_words_groups_lines_and_leaves_the_streamer_alone():
    track = head_track(samples((0.0, [npc(959)]), (3.0, [npc(959)])), None, CROP)
    words = [{"text": "oi", "start": 0.0, "end": 0.4, "role": "streamer"},
             {"text": "larga", "start": 1.0, "end": 1.3, "role": "game"},
             {"text": "isso", "start": 1.4, "end": 1.8, "role": "game"}]
    out = annotate_npc_words(words, track)
    assert "npc_path" not in out[0] and out[0]["seat_path"] and out[1]["npc_path"] == out[2]["npc_path"]
    assert out[1]["npc_path"][0][0] == 1.0 and out[1]["npc_path"][-1][1] == 1.8


def test_captions_draw_npc_moves(tmp_path):
    from src.media.captions import npc_events
    word = {"text": "larga", "start": 1.0, "end": 2.0, "role": "game", "npc_path": [(1.0, 2.0, 100, 1000, 300, 1000)]}
    events = npc_events([word], 1.0, 2.0, "LARGA", "{\\c&HFFFFFF&}")
    assert len(events) == 1 and "\\an2" in events[0] and "\\move(100,1000,300,1000)" in events[0]


def test_a_short_pause_keeps_one_npc_line():
    # the caption builder only splits a line on pauses over 1 s: the NPC path must cover the whole line
    track = head_track(samples((0.0, [npc(959)]), (3.0, [npc(959)])), None, CROP)
    words = [{"text": "larga", "start": 1.0, "end": 1.3, "role": "game"},
             {"text": "isso", "start": 2.1, "end": 2.5, "role": "game"}]
    out = annotate_npc_words(words, track)
    assert out[0]["npc_path"] is out[1]["npc_path"] and out[1]["npc_path"][-1][1] == 2.5


def test_npc_events_keep_the_whole_text_on_screen():
    from src.media.captions import npc_events
    word = {"text": "larga", "start": 1.0, "end": 2.0, "role": "game", "npc_path": [(1.0, 2.0, EDGE_X, 1300, EDGE_X, 1300)]}
    events = npc_events([word], 1.0, 2.0, "LARGA ISSO AGORA", "", half_width=300)
    assert "\\pos(330,1300)" in events[0]  # 300 px half text + 30 px margin


def test_npc_events_hold_until_the_chunk_ends():
    from src.media.captions import npc_events
    word = {"text": "x", "start": 1.0, "end": 1.3, "role": "game", "npc_path": [(1.0, 1.3, 500, 1300, 500, 1300)]}
    events = npc_events([word], 1.0, 2.5, "X Y", "")
    assert events[-1].startswith("Dialogue: 0,0:00:01.30,0:00:02.50") or "0:00:02.50" in events[-1]


def test_a_broken_speaker_check_falls_back_to_the_nearest_face():
    track = head_track(samples((0.0, [npc(650), npc(1250)])), None, CROP)
    def broken(*args):
        raise RuntimeError("no video")
    path = line_path(0.0, 0.5, track, broken)
    assert abs(path[0][2] - round((1250 - 554) * 1080 / 812)) <= 2  # 1250 lands nearer the centre


def test_the_protagonist_and_the_phone_talk_from_over_the_player_and_split_from_npcs():
    from src.media.npc_captions import PLAYER_SPOT
    track = head_track(samples((0.0, [npc(959)]), (3.0, [npc(959)])), None, CROP)
    words = [{"text": "where", "start": 1.0, "end": 1.3, "role": "game", "voice": "protagonist"},
             {"text": "here", "start": 1.4, "end": 1.6, "role": "game", "voice": "phone", "caller": "Jason"},
             {"text": "move", "start": 1.7, "end": 2.0, "role": "game", "voice": "npc"}]
    out = annotate_npc_words(words, track)
    assert out[0]["npc_path"] == [(1.0, 1.3, *PLAYER_SPOT, *PLAYER_SPOT)]
    assert out[1]["npc_path"] == [(1.4, 1.6, *PLAYER_SPOT, *PLAYER_SPOT)]
    assert out[2]["npc_path"][0][:2] == (1.7, 2.0) and out[2]["npc_path"][0][2:4] != PLAYER_SPOT


def test_the_streamer_s_caption_climbs_onto_the_seam_once_the_title_is_gone():
    from src.media.catclips3 import GAME_Y, TITLE_OUT
    from src.media.npc_captions import streamer_path
    path = streamer_path()
    assert segments_between(path, 0.0, 1.0)[0][3] > GAME_Y + 100  # under the title badge
    assert segments_between(path, TITLE_OUT + 1, TITLE_OUT + 2)[0][3] == GAME_Y  # on the seam after


def test_an_npc_line_never_points_at_the_protagonist():
    from src.media.npc_captions import PLAYER_SPOT
    # a face right where the crop centres the player, and one NPC off to the side
    cx, cy, cw, ch = CROP
    scale = cw / 1080
    player = (cx + PLAYER_SPOT[0] * scale - 20, cy + (PLAYER_SPOT[1] - 700 + 40) * scale, 40, 50)
    track = head_track([(0.0, [player, npc(1000)]), (3.0, [player, npc(1000)])], None, CROP)
    assert all(abs(x - PLAYER_SPOT[0]) > 120 for _t, pts in track for x, _y, _f in pts)
