import numpy as np

from src.media.call_card import badge_on, caller, spans


def test_the_caller_comes_off_a_running_call_card():
    # tesseract's reads of GTA 6's card on xQc's club clip
    assert caller('* Jason | "00:02'.replace(" | ", "\n")) == "Jason"
    assert caller("\\% Jason\n* 00:04") == "Jason"
    assert caller("*% Jason\n% Call Ended") is None
    assert caller("Jason\n* — Dialing...") is None  # nobody on the line yet
    assert caller("SELECT &\nEXIT ©") is None  # the pause menu


def test_the_green_badge_gates_the_card():
    frame = np.zeros((1080, 1920, 3), np.uint8)
    assert not badge_on(frame)
    frame[1000:1020, 1500:1520] = (60, 200, 40)  # BGR: the badge's green
    assert badge_on(frame)


def test_samples_of_one_call_join_and_a_new_caller_starts_another():
    times = [0.0, 1.0, 2.0, 3.0, 9.0, 10.0, 11.0]
    names = ["Jason", "Jason", None, "Jason", "Brian", None, "Brian"]
    assert spans(times, names) == [(0.0, 3.5, "Jason"), (8.5, 11.5, "Brian")]
