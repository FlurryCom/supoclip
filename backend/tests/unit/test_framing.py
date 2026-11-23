from src.media.framing import box_to_rect, rect_to_box


def test_box_to_rect_is_even_and_inside():
    assert box_to_rect({"x": 0.7, "y": 0.02, "w": 0.28, "h": 0.36}, 1920, 1080) == (1344, 22, 538, 388)
    x, y, w, h = box_to_rect({"x": 0.96, "y": 0.96, "w": 0.04, "h": 0.04}, 1921, 1081)
    assert x + w <= 1921 and y + h <= 1081 and w % 2 == h % 2 == x % 2 == y % 2 == 0


def test_same_box_same_relative_crop_at_any_resolution():
    box = {"x": 0.25, "y": 0.5, "w": 0.5, "h": 0.25}
    assert box_to_rect(box, 1280, 720) == (320, 360, 640, 180)
    assert box_to_rect(box, 1920, 1080) == (480, 540, 960, 270)


def test_rect_to_box_round_trips():
    assert rect_to_box((480, 540, 960, 270), 1920, 1080) == {"x": 0.25, "y": 0.5, "w": 0.5, "h": 0.25}
