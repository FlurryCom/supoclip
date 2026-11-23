from pathlib import Path

from src.edit_kits import KITS, kit_choices
from src.media.catclips3 import build_v3_filter, game_crop, title_bar


def _inside(rect, width, height):
    x, y, w, h = rect
    return x >= 0 and y >= 0 and x + w <= width and y + h <= height and x % 2 == y % 2 == w % 2 == h % 2 == 0


def _overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def test_game_crop_any_resolution():
    assert game_crop(1920, 1080) == (500, 0, 918, 918)  # 85% of the height, centred
    for width, height in ((1280, 720), (1440, 1080)):
        rect = game_crop(width, height)
        assert rect[2] == rect[3] == (int(height * 0.85) // 2) * 2 and _inside(rect, width, height)


def test_game_crop_avoids_the_webcam():
    corner = (0, 666, 560, 414)  # a bottom-left cam reaching into the centre crop
    rect = game_crop(1920, 1080, cam_rect=corner)
    assert not _overlap(rect, corner) and _inside(rect, 1920, 1080) and rect[2] == 918
    middle = (700, 700, 500, 380)  # a cam under the centre: the crop gets shorter instead
    rect = game_crop(1920, 1080, cam_rect=middle)
    assert not _overlap(rect, middle) and _inside(rect, 1920, 1080)


def test_game_crop_uses_the_game_box():
    x, y, w, h = game_crop(1920, 1080, game_rect=(0, 0, 1600, 1080))
    assert abs(x + w / 2 - 800) <= 2


def test_title_bar_falls_back_without_assets(tmp_path):
    bar = title_bar("Ginge tentou assaltar o posto", tmp_path / "missing.otf", tmp_path / "missing.webp")
    assert "0xFEC803" in bar and "ArchivoBlack" in bar and "movie=" not in bar


def test_title_bar_escapes_and_joins(tmp_path):
    font, background = tmp_path / "pricedown.otf", tmp_path / "palms.webp"
    font.write_bytes(b"x")
    background.write_bytes(b"x")
    bar = title_bar("50% é: 'isso'", font, background)
    assert "movie=" in bar and "‌" in bar and str(font) in bar
    text = bar.split(":text=")[1].split(":fontcolor")[0]
    assert all(text[i - 1] == "\\" for i, c in enumerate(text) if c == ":")  # every colon escaped


def test_v3_filter_heights(tmp_path):
    graph = build_v3_filter(1920, 1080, None, "x", (0, 452, 396, 318), (500, 0, 918, 918),
                            font=tmp_path / "none.otf", background=tmp_path / "none.webp")
    assert "scale=1080:640" in graph and "crop=918:918:500:0" in graph and "scale=1080:1080" in graph
    assert graph.endswith("[vout]") and "vstack=inputs=2" in graph


def test_v3_kit_hidden_until_approved():
    assert KITS["catclips_v3"]["layout"] == "catclips3"
    assert "catclips_v3" not in {kit["id"] for kit in kit_choices()}
