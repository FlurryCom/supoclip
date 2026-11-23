from pathlib import Path

from src.edit_kits import KITS, kit_choices
from src.media.catclips3 import GAME_H, build_v3_filter, game_crop, title_badge


def _inside(rect, width, height):
    x, y, w, h = rect
    return x >= 0 and y >= 0 and x + w <= width and y + h <= height and x % 2 == y % 2 == w % 2 == h % 2 == 0


def _overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def test_game_crop_any_resolution():
    assert game_crop(1920, 1080) == (554, 0, 812, 918)  # 85% of the height, the output's 1080x1220 shape
    for width, height in ((1280, 720), (1440, 1080)):
        x, y, w, h = rect = game_crop(width, height)
        assert h == (int(height * 0.85) // 2) * 2 and abs(w / h - 1080 / GAME_H) < 0.01 and _inside(rect, width, height)


def test_game_crop_avoids_the_webcam():
    corner = (0, 666, 560, 414)  # a bottom-left cam reaching into the centre crop
    rect = game_crop(1920, 1080, cam_rect=corner)
    assert not _overlap(rect, corner) and _inside(rect, 1920, 1080) and rect[3] == 918
    middle = (700, 700, 500, 380)  # a cam under the centre: the crop gets shorter instead
    rect = game_crop(1920, 1080, cam_rect=middle)
    assert not _overlap(rect, middle) and _inside(rect, 1920, 1080)


def test_game_crop_uses_the_game_box():
    x, y, w, h = game_crop(1920, 1080, game_rect=(0, 0, 1600, 1080))
    assert abs(x + w / 2 - 800) <= 2


def test_title_badge_falls_back_without_assets(tmp_path):
    bar = title_badge("Ginge tentou assaltar o posto", tmp_path / "missing.otf", tmp_path / "missing.webp")
    assert "0xFEC803" in bar and "ArchivoBlack" in bar and "missing.webp" not in bar


def test_title_badge_escapes_and_joins(tmp_path):
    font, background = tmp_path / "pricedown.otf", tmp_path / "palms.webp"
    font.write_bytes(b"x")
    background.write_bytes(b"x")
    bar = title_badge("50% é: 'isso'", font, background)
    assert "movie=" in bar and "‌" in bar and str(font) in bar
    text = bar.split(":text=")[1].split(":fontcolor")[0]
    assert all(text[i - 1] == "\\" for i, c in enumerate(text) if c == ":")  # every colon escaped


def test_v3_filter_heights(tmp_path):
    graph = build_v3_filter(1920, 1080, None, "x", (0, 452, 396, 318), (554, 0, 812, 918),
                            font=tmp_path / "none.otf", background=tmp_path / "none.webp")
    assert "scale=1080:700" in graph and "crop=812:918:554:0" in graph and "scale=1080:1220" in graph
    assert graph.endswith("[vout]") and "vstack=inputs=2" in graph


def test_title_badge_balloons_in_and_is_sucked_away_after_three_seconds(tmp_path):
    font, background = tmp_path / "pricedown.otf", tmp_path / "palms.webp"
    font.write_bytes(b"x")
    background.write_bytes(b"x")
    badge = title_badge("GTA 6", font, background)
    assert "eval=frame" in badge and "0.1+8.5*t" in badge  # grows in, like a balloon
    assert "max(0.02\\,1-3.9*(t-3))" in badge and "400*(t-3)" in badge  # shrinks while rising
    assert "enable='lt(t\\,3.25)'" in badge
    assert "rotate=" in badge and "between(X" in badge  # slanted and tilted
    assert "cat.png" in badge and "dots.png" in badge and badge.endswith("[vout]")


def test_v3_kit_hidden_until_approved():
    assert KITS["catclips_v3"]["layout"] == "catclips3"
    assert "catclips_v3" not in {kit["id"] for kit in kit_choices()}


def test_title_badge_falls_back_on_a_path_ffmpeg_would_misread(tmp_path):
    font, background = tmp_path / "pricedown.otf", tmp_path / "palms'1.webp"
    font.write_bytes(b"x")
    background.write_bytes(b"x")
    assert "palms'1" not in title_badge("x", font, background)


def test_title_fills_the_bar_with_pricedown_s_own_widths():
    from src.media.catclips3 import BADGE_H, BADGE_MAX_W, TITLE_W, badge_width, fit_title, pricedown_width
    lines, size = fit_title("xQc refuses to leave the club in GTA 6")
    assert len(lines) == 2 and size >= 85  # was 64: sized with Archivo Black's much wider letters
    assert all(pricedown_width(line, size) <= TITLE_W for line in lines)
    assert 2 * 0.639 * size + 0.25 * size <= BADGE_H - 30  # both lines' capitals inside the badge
    assert badge_width(lines, size) <= BADGE_MAX_W
    short, big = fit_title("GTA 6")
    assert short == ["GTA 6"] and big > size and 0.639 * big <= BADGE_H - 30
    assert badge_width(short, big) < BADGE_MAX_W  # a short title gets a shorter badge


def test_title_lines_are_balanced():
    from src.media.catclips3 import fit_title, pricedown_width
    lines, size = fit_title("Ginge tentou assaltar o posto e levou uma surra do caixa")
    widths = [pricedown_width(line, size) for line in lines]
    assert len(lines) == 2 and min(widths) >= 0.6 * max(widths)
