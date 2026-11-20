import subprocess

import pytest

from src.media.streamer_split import (
    BAR_H, GAME_H, TOP_H, build_split_filter, cam_crop, find_streamer_cam, game_crop, title_lines,
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


def test_title_lines_wrap_to_two_and_shrink():
    lines, size = title_lines("Hiding underwater with the whole map hunting us")
    assert lines == ["HIDING UNDERWATER WITH THE", "WHOLE MAP HUNTING US"] and size <= 64
    lines, size = title_lines("Short")
    assert lines == ["SHORT"] and size == 64
    lines, _ = title_lines("word " * 40)
    assert len(lines) == 2


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
