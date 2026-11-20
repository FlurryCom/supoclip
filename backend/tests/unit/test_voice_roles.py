import numpy as np

from src.media import voice_roles as vr


def word(start, end, text="hi"):
    return {"start": int(start * 1000), "end": int(end * 1000), "text": text}


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
