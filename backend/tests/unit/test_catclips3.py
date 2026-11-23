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


def test_title_badge_drips_in_and_is_sucked_away_after_three_seconds(tmp_path):
    from src.media import catclips3 as c3
    font, background = tmp_path / "pricedown.otf", tmp_path / "palms.webp"
    font.write_bytes(b"x")
    background.write_bytes(b"x")
    badge = c3.title_badge("GTA 6", font, background)
    assert "eval=frame" in badge and c3.SX in badge and c3.SY in badge and c3.DY in badge
    assert "setpts=N/60/TB" in badge and "enable='lt(t\\,3.32)'" in badge
    assert "rotate=" in badge and "between(X" in badge  # slanted and tilted
    assert "cat.png" in badge and "dots.png" in badge and badge.endswith("[vout]")


def test_the_drip_curves_join_without_jumps():
    from src.media import catclips3 as c3

    def at(expr, t):
        import math
        code = expr.replace("\\,", ",")
        env = {"t": t, "exp": math.exp, "cos": math.cos, "pow": pow, "min": min, "max": max,
               "lt": lambda a, b: a < b, "if": None}
        code = code.replace("if(", "iff(")
        env["iff"] = lambda c, a, b: a if c else b
        return eval(code, env)

    for curve in (c3.SX, c3.SY, c3.DY):
        for edge in (0.2, 3.0):
            assert abs(at(curve, edge - 1e-6) - at(curve, edge + 1e-6)) < 0.01
    assert at(c3.SX, 0.0) < 0.5 and abs(at(c3.SX, 2.5) - 1) < 0.01 and at(c3.SY, 0.2) > 1.1  # lands stretched
    assert at(c3.SX, 3.32) <= 0.03 and at(c3.DY, 3.32) < -150  # sucked away upward


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


def test_dark_band_finds_gta_s_cinema_bars_in_most_frames():
    import numpy as np
    from src.media.catclips3 import dark_band
    scene = np.full((300, 400), 90, dtype=np.uint8)
    barred = scene.copy()
    barred[:68] = 3  # cutscene: black bar on top
    dark_club = scene.copy()
    dark_club[:40] = 10
    dark_club[:40, 200:210] = 200  # a dim scene with a neon light: not a bar
    assert dark_band([barred, barred, scene]) == 68
    assert dark_band([scene, scene, barred]) == 0
    assert dark_band([dark_club, dark_club]) == 0


def test_without_letterbox_cuts_below_the_bars_keeping_the_shape(tmp_path, monkeypatch):
    import src.media.catclips3 as c3
    monkeypatch.setattr(c3, "dark_band", lambda tops: 68)

    class Cap:
        def __init__(self, path): pass
        def get(self, prop): return 100
        def set(self, prop, value): pass
        def read(self):
            import numpy as np
            return True, np.zeros((1080, 1920, 3), dtype=np.uint8)
        def release(self): pass
    import cv2
    monkeypatch.setattr(cv2, "VideoCapture", Cap)
    x, y, w, h = c3.without_letterbox(tmp_path / "clip.mp4", (554, 0, 812, 918))
    assert y == 68 and h == 850 and abs(w / h - 1080 / 1220) < 0.01
    assert abs((x + w / 2) - (554 + 812 / 2)) <= 2 and x % 2 == y % 2 == w % 2 == h % 2 == 0
