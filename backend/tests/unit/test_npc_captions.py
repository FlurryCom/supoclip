from src.media.npc_captions import EDGE_X, FIXED, annotate_npc_words, head_track, line_path, segments_between

CROP = (500, 0, 918, 918)  # square game cut of a 1920x1080 source -> 1080x1080 at y=840
CAM_FACE = (150.0, 500.0, 80.0, 110.0)  # webcam face, outside the crop


def npc(x, y=300.0, size=120.0):
    return (x - size / 2, y, size, size * 1.2)  # face box centred on x


def samples(*frames):
    return [(t, faces) for t, faces in frames]


def test_npc_line_sits_above_the_visible_face():
    track = head_track(samples((0.0, [npc(959)]), (0.5, [npc(959)]), (1.0, [npc(959)])), None, CROP)
    path = line_path(0.0, 1.0, track)
    x, y = path[0][2], path[0][3]
    assert abs(x - 540) <= 2  # face centre 959 -> middle of the 1080 output
    assert 840 + 100 <= y < 840 + (300 - 0) * 1080 / 918  # above the head, inside the game area


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
    assert line_path(0.0, 1.0, track)[0][3] >= 840 + 100


def test_segments_between_cuts_a_path_at_chunk_bounds():
    path = [(0.0, 2.0, 100, 1000, 300, 1000)]
    assert segments_between(path, 1.0, 2.0) == [(1.0, 2.0, 200, 1000, 300, 1000)]


def test_annotate_npc_words_groups_lines_and_leaves_the_streamer_alone():
    track = head_track(samples((0.0, [npc(959)]), (3.0, [npc(959)])), None, CROP)
    words = [{"text": "oi", "start": 0.0, "end": 0.4, "role": "streamer"},
             {"text": "larga", "start": 1.0, "end": 1.3, "role": "game"},
             {"text": "isso", "start": 1.4, "end": 1.8, "role": "game"}]
    out = annotate_npc_words(words, track)
    assert "npc_path" not in out[0] and out[1]["npc_path"] == out[2]["npc_path"]
    assert out[1]["npc_path"][0][0] == 1.0 and out[1]["npc_path"][-1][1] == 1.8


def test_captions_draw_npc_moves(tmp_path):
    from src.media.captions import npc_events
    word = {"text": "larga", "start": 1.0, "end": 2.0, "role": "game", "npc_path": [(1.0, 2.0, 100, 1000, 300, 1000)]}
    events = npc_events([word], 1.0, 2.0, "LARGA", "{\\c&HFFFFFF&}")
    assert len(events) == 1 and "\\an2" in events[0] and "\\move(100,1000,300,1000)" in events[0]
