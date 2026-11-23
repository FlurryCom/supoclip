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


import pytest

from src.media.framing import Verdict, decide, quick_check

CAM, GAME, FULL, NEW = (0, 452, 396, 318), (0, 0, 1920, 1080), (0, 0, 1920, 1080), (1500, 40, 380, 300)
FACE_IN_CAM = (150.0, 500.0, 80.0, 110.0)
FACE_ELSEWHERE = (900.0, 300.0, 80.0, 110.0)


def test_quick_check_passes_when_the_face_stays_in_the_cam():
    assert quick_check([(i, [FACE_IN_CAM, FACE_ELSEWHERE]) for i in range(8)], CAM)


def test_quick_check_fails_when_the_face_left_the_cam():
    assert not quick_check([(i, [FACE_ELSEWHERE]) for i in range(8)], CAM)
    assert not quick_check([(i, [FACE_IN_CAM] if i < 3 else []) for i in range(8)], CAM)
    assert not quick_check([], CAM)


class Effects:
    def __init__(self, claim=True, verdict=None, swap=True):
        self._claim, self._verdict, self._swap = claim, verdict, swap
        self.calls = []

    def claim(self, failed):
        self.calls.append(("claim", failed))
        return self._claim

    def verdict(self, proposal):
        self.calls.append(("verdict", proposal))
        if isinstance(self._verdict, Exception):
            raise self._verdict
        return self._verdict

    def swap(self, cam, game, reason):
        self.calls.append(("swap", cam, game))
        return self._swap

    def flag(self, reason):
        self.calls.append(("flag",))

    def release(self):
        self.calls.append(("release",))

    def run(self, cam=CAM, game=GAME, marked=True, auto_off=False, passed=True, proposal=NEW):
        return decide(cam, game, marked, auto_off, passed, lambda: proposal, FULL,
                      self.claim, self.verdict, self.swap, self.flag, self.release)

    def names(self):
        return [c[0] for c in self.calls]


def v(current=False, proposal=False, webcam=True, normal=True):
    return Verdict(normal_scene=normal, current_ok=current, proposal_ok=proposal, has_webcam=webcam, reason="motivo")


def test_no_check_due_keeps_the_channel_boxes():
    fx = Effects(claim=None)
    d = fx.run(passed=False)
    assert (d.kind, d.cam, d.game) == ("channel", CAM, GAME) and fx.names() == ["claim"]
    assert fx.calls[0] == ("claim", True)  # a failed local check may ask again after 30 min


def test_unmarked_channel_without_a_check_uses_the_detector():
    d = Effects(claim=None).run(cam=None, game=None, marked=False)
    assert d.kind == "detector" and d.game is None


def test_claude_ok_keeps_the_channel_boxes():
    fx = Effects(verdict=v(current=True))
    assert fx.run().kind == "channel" and "swap" not in fx.names()


def test_claude_approves_the_proposal_swaps_only_the_webcam():
    fx = Effects(verdict=v(proposal=True))
    d = fx.run()
    assert (d.kind, d.cam, d.game) == ("proposal", NEW, GAME) and ("swap", NEW, GAME) in fx.calls


def test_unmarked_channel_gets_marked_with_the_whole_game():
    fx = Effects(verdict=v(proposal=True))
    d = fx.run(cam=None, game=None, marked=False)
    assert (d.kind, d.cam, d.game) == ("proposal", NEW, FULL) and ("swap", NEW, FULL) in fx.calls


def test_a_stream_without_webcam_is_marked_as_such():
    fx = Effects(verdict=v(webcam=False))
    d = fx.run(cam=None, game=None, marked=False, proposal=None)
    assert (d.kind, d.cam, d.game) == ("no_cam", None, FULL) and ("swap", None, FULL) in fx.calls


def test_automatic_changes_off_only_warns_and_keeps_the_owners_boxes():
    fx = Effects(verdict=v(proposal=True))
    d = fx.run(auto_off=True)
    assert d.kind == "channel" and "swap" not in fx.names() and "flag" in fx.names()


def test_no_good_proposal_warns_and_uses_the_detector():
    fx = Effects(verdict=v())
    d = fx.run()
    assert (d.kind, d.cam, d.game) == ("detector", None, GAME) and "flag" in fx.names() and "swap" not in fx.names()


def test_claude_error_retries_later_and_keeps_the_channel_boxes():
    fx = Effects(verdict=RuntimeError("cli"))
    assert fx.run().kind == "channel" and fx.names()[-1] == "release"


def test_starting_soon_screen_retries_later_without_deciding():
    fx = Effects(verdict=v(normal=False))
    assert fx.run().kind == "channel" and fx.names()[-1] == "release" and "swap" not in fx.names()


def test_owner_saved_meanwhile_keeps_the_channel_boxes():
    fx = Effects(verdict=v(proposal=True), swap=False)
    assert fx.run().kind == "channel"


def test_ask_claude_sends_the_proposal_as_a_second_image():
    import numpy as np
    from src.media import framing
    seen = {}

    def run(prompt, images, schema, model, timeout):
        seen.update(prompt=prompt, images=images, model=model, schema=schema)
        return {"normal_scene": True, "current_ok": False, "proposal_ok": True, "has_webcam": True, "reason": "ok",
                "webcam_box": {"x": 0, "y": 0, "w": 0, "h": 0}}

    frame = np.zeros((1080, 1920, 3), np.uint8)
    answer = framing.ask_claude(frame, "cam", CAM, GAME, NEW, run=run)
    assert answer == Verdict(True, False, True, True, "ok")
    assert len(seen["images"]) == 2 and all(i[:2] == b"\xff\xd8" for i in seen["images"])
    assert seen["model"] == framing.CHECK_MODEL and "Image 2" in seen["prompt"]
    framing.ask_claude(frame, "none", None, None, None, run=run)
    assert len(seen["images"]) == 1 and "always false" in seen["prompt"]


CLAUDES = (0, 650, 570, 430)


def test_a_bad_proposal_falls_back_to_the_box_claude_saw_once_confirmed():
    class Two(Effects):
        def verdict(self, proposal):
            self.calls.append(("verdict", proposal))
            if proposal == CLAUDES:
                return v(proposal=True)
            return Verdict(True, False, False, True, "motivo", webcam=CLAUDES)
    fx = Two()
    d = fx.run()
    assert (d.kind, d.cam, d.game) == ("proposal", CLAUDES, GAME) and ("swap", CLAUDES, GAME) in fx.calls
    assert [c for c in fx.calls if c[0] == "verdict"] == [("verdict", NEW), ("verdict", CLAUDES)]


def test_claudes_box_not_confirmed_only_warns():
    fx = Effects(verdict=Verdict(True, False, False, True, "motivo", webcam=CLAUDES))
    d = fx.run()
    assert d.kind == "detector" and "flag" in fx.names() and "swap" not in fx.names()


def test_claudes_box_is_read_as_fractions_of_the_frame():
    from src.media.framing import claude_box
    assert claude_box({"x": 0, "y": 0.6, "w": 0.3, "h": 0.4}, 1920, 1080) == (0, 648, 576, 432)
    assert claude_box({"x": 0, "y": 0, "w": 0, "h": 0}, 1920, 1080) is None  # no webcam
    assert claude_box({"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5}, 1920, 1080) == (1728, 972, 192, 108)  # clipped to the frame
    assert claude_box({"x": -0.1, "y": 0, "w": 0.3, "h": 0.5}, 1920, 1080) == (0, 0, 384, 540)  # cut, not shifted
    assert claude_box({"x": "nan", "y": 0, "w": 0.3, "h": 0.5}, 1920, 1080) is None
    assert claude_box({"x": 0, "y": 0, "w": 0.045, "h": 0.5}, 1920, 1080) is None  # too thin to be a webcam
