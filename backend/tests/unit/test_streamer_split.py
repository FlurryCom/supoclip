import subprocess

import pytest

from src.media.streamer_split import (
    BAR_H, GAME_H, TITLE_MAX_W, TOP_H, build_split_filter, cam_crop, find_streamer_cam, game_crop,
    title_lines, title_width,
)

W, H = 1920, 1080
CAM = (180.0, 700.0, 160.0, 180.0)  # webcam face, bottom-left


def samples_with(cam_every=1, n=20, moving=True):
    out = []
    for i in range(n):
        faces = []
        if i % cam_every == 0:
            faces.append((CAM[0] + (i % 3), CAM[1] - (i % 2), CAM[2], CAM[3]))
        if moving:
            faces.append((900.0 + i * 40, 300.0, 60.0, 70.0))  # a game character walking by
        out.append((i * 0.5, faces))
    return out


def test_find_streamer_cam_in_a_bottom_corner():
    box = find_streamer_cam(W, H, samples_with())
    assert box is not None and abs(box[0] - CAM[0]) < 5 and abs(box[1] - CAM[1]) < 5


def test_no_cam_when_no_face_stays_put():
    assert find_streamer_cam(W, H, samples_with(cam_every=4)) is None
    assert find_streamer_cam(W, H, [(0.0, [CAM])] * 3) is None  # too few samples


def test_cam_crop_keeps_the_face_inside_a_3_by_2_box():
    x, y, w, h = cam_crop(W, H, CAM)
    assert abs(w / h - 1080 / TOP_H) < 0.02
    assert x <= CAM[0] and CAM[0] + CAM[2] <= x + w and y <= CAM[1] and CAM[1] + CAM[3] <= y + h
    assert x >= 0 and y >= 0 and x + w <= W and y + h <= H and w % 2 == 0 and h % 2 == 0


def test_game_crop_avoids_the_cam_side():
    x, y, w, h = game_crop(W, H, CAM)
    assert (y, h) == (0, H) and abs(w / h - 1080 / GAME_H) < 0.01
    assert x == W - w  # cam on the left: take the right side
    assert game_crop(W, H, (1600.0, 100.0, 100.0, 120.0))[0] == 0
    assert game_crop(W, H, None)[0] == (W - w) // 2 // 2 * 2


def _fits(lines, size):
    return all(title_width(line, size) <= TITLE_MAX_W for line in lines)


@pytest.mark.parametrize("title", [
    "Texas Venn diagram joke about Lucia",          # was cut at both edges on Instagram
    "This is the final battle, bro",
    "Hiding underwater with the whole map hunting us",
    "WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW",  # one huge word
    "word " * 40,
])
def test_title_always_fits_inside_the_margins(title):
    lines, size = title_lines(title)
    assert 1 <= len(lines) <= 2 and _fits(lines, size) and size >= 30


def test_title_lines_keep_short_titles_big_and_drop_emoji():
    assert title_lines("Short") == (["SHORT"], 64)
    lines, _ = title_lines("Coach, trust me 👀🔥")
    assert lines == ["COACH, TRUST ME"]


def test_overlong_title_ends_with_an_ellipsis():
    lines, size = title_lines("word " * 40)
    assert lines[-1].endswith("…") and _fits(lines, size)


@pytest.mark.parametrize("cam", [CAM, None])
def test_filter_renders_a_1080x1920_frame(tmp_path, cam):
    src, out = tmp_path / "src.mp4", tmp_path / "out.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=s={W}x{H}:r=10:d=1",
                    str(src)], check=True)
    graph = build_split_filter(W, H, cam, "It's 100%: a, b; [c] \\ done")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex", graph,
                    "-map", "[vout]", "-frames:v", "1", str(out)], check=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                            str(out)], capture_output=True, text=True, check=True).stdout.strip()
    assert probe == "1080,1920"
    assert TOP_H + BAR_H + GAME_H == 1920


def test_title_is_white_with_a_black_border():
    graph = build_split_filter(W, H, CAM, "Big moment")
    assert "fontcolor=0xFFFFFF" in graph and "bordercolor=0x111111" in graph and "borderw=" in graph


def test_captions_leave_a_gap_under_the_bar():
    from src.media.streamer_split import CAPTION_Y
    # caption centre; about half a caption line plus a visible gap below the bar
    assert CAPTION_Y * 1920 - (TOP_H + BAR_H) >= 95


def _stream_with_cam(path, rect, border=True, frames=24, extra=None):
    """Moving 'gameplay' noise with a webcam rectangle (still-ish content, light border) in it."""
    import numpy as np
    x, y, w, h = rect
    yy, xx = np.mgrid[0:H, 0:W]
    raw = bytearray()
    for i in range(frames):
        # Smooth, moving 'gameplay': no lasting straight edges of its own.
        shade = ((xx * 0.13 + yy * 0.07 + i * 23) % 200 + 20).astype(np.uint8)
        frame = np.repeat(shade[:, :, None], 3, axis=2)
        frame[y:y + h, x:x + w] = (60 + i % 5, 70, 80)
        if border:
            frame[y:y + 6, x:x + w] = frame[y + h - 6:y + h, x:x + w] = 230
            frame[y:y + h, x:x + 6] = frame[y:y + h, x + w - 6:x + w] = 230
        if extra:  # another strong edge farther from the face (a sub counter, a HUD bar)
            ex, ey, ew, eh = extra
            frame[ey - 30:ey, ex:ex + ew] = 0  # black then white: a much stronger edge than the cam's
            frame[ey:ey + eh, ex:ex + ew] = 255
        raw += frame.tobytes()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                    "-r", "8", "-i", "-", "-pix_fmt", "yuv420p", "-crf", "12", str(path)], input=bytes(raw), check=True)
    return path


def test_finds_the_whole_webcam_rectangle(tmp_path):
    from src.media.streamer_split import find_cam_rect
    rect = (40, 560, 560, 400)  # bottom-left webcam
    face = (240.0, 640.0, 150.0, 170.0)
    found = find_cam_rect(_stream_with_cam(tmp_path / "s.mp4", rect), W, H, face)
    assert found is not None
    assert all(abs(a - b) <= 14 for a, b in zip(found, rect)), found  # within the 6 px border, either edge


def test_no_rectangle_without_edges(tmp_path):
    from src.media.streamer_split import find_cam_rect
    path = _stream_with_cam(tmp_path / "n.mp4", (40, 560, 560, 400), border=False)
    # Flat cam content against noise still has edges; a face floating in plain noise has none.
    noise = tmp_path / "noise.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"nullsrc=s={W}x{H}:r=8:d=3",
                    "-vf", "geq=random(1)*255:128:128", str(noise)], check=True)
    assert find_cam_rect(noise, W, H, (240.0, 640.0, 150.0, 170.0)) is None
    assert path.exists()


def test_flat_moving_background_has_no_rectangle(tmp_path):
    from src.media.streamer_split import find_cam_rect
    plain = _stream_with_cam(tmp_path / "p.mp4", (0, 0, 2, 2), border=False)
    assert find_cam_rect(plain, W, H, (240.0, 640.0, 150.0, 170.0)) is None


def test_whole_cam_is_fitted_not_cropped(tmp_path):
    src, out = tmp_path / "src.mp4", tmp_path / "out.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=s={W}x{H}:r=10:d=1",
                    str(src)], check=True)
    graph = build_split_filter(W, H, CAM, "Big moment", cam_rect=(40, 560, 560, 400))
    assert "crop=560:400:40:560" in graph and "force_original_aspect_ratio=decrease" in graph
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex", graph,
                    "-map", "[vout]", "-frames:v", "1", str(out)], check=True)


def test_picks_the_cam_edge_nearest_the_face_not_a_stronger_one_beyond(tmp_path):
    from src.media.streamer_split import find_cam_rect
    rect = (40, 300, 560, 400)
    face = (240.0, 380.0, 150.0, 170.0)
    found = find_cam_rect(_stream_with_cam(tmp_path / "s.mp4", rect, extra=(40, 900, 560, 40)), W, H, face)
    assert found is not None and abs(found[1] + found[3] - 700) <= 14, found


def test_gameplay_is_the_whole_16_9_frame_not_a_crop(tmp_path):
    # Owner: the bottom shows the whole game screen in 16:9 (even if the webcam shows up again in it).
    graph = build_split_filter(W, H, CAM, "Big moment", cam_rect=(40, 560, 560, 400))
    game = [part for part in graph.split(";") if part.startswith("[game_src]") or "[game_fg]" in part]
    assert any("scale=1080:1000:force_original_aspect_ratio=decrease" in part for part in game)
    assert not any(f"crop={1166}:" in part for part in game)  # no side crop of the gameplay any more


def test_captions_sit_between_the_bar_and_the_game():
    from src.media.streamer_split import CAPTION_Y
    game_top = TOP_H + BAR_H + (GAME_H - 608) // 2
    assert TOP_H + BAR_H < CAPTION_Y * 1920 < game_top
