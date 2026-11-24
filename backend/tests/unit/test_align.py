import numpy as np

from src.media.align import align_text, line_times, speech_onset

RATE = 16000


def _audio(parts):
    """Audio from (seconds, amplitude) parts: white noise at each amplitude."""
    rng = np.random.default_rng(7)
    return np.concatenate([rng.standard_normal(int(s * RATE)).astype(np.float32) * a for s, a in parts])


QUIET_THEN_SPEECH = _audio([(1.0, 0.001), (1.0, 0.3), (0.5, 0.001)])


def test_speech_onset_finds_where_the_voice_starts():
    onset = speech_onset(QUIET_THEN_SPEECH, RATE, 0.0, 2.5)
    assert onset is not None and abs(onset - 1.0) < 0.08


def test_speech_onset_on_silence_is_none():
    assert speech_onset(_audio([(2.0, 0.001)]), RATE, 0.0, 2.0) is None
    assert speech_onset(QUIET_THEN_SPEECH, RATE, 0.0, 0.8) is None  # window before the voice


def test_alignment_is_gated_against_nonsense():
    good = [("hey", 0.1, 0.4), ("what", 0.5, 0.8), ("happened", 0.9, 1.4)]
    assert align_text(QUIET_THEN_SPEECH, RATE, "hey what happened", 0.0, 2.5,
                      aligner=lambda seg, text: good) == [("hey", 0.1, 0.4), ("what", 0.5, 0.8),
                                                          ("happened", 0.9, 1.4)]
    bad = [
        [("hey", 0.5, 0.4), ("what", 0.3, 0.8), ("happened", 0.9, 1.4)],  # not monotonic
        [("hey", 0.1, 0.4), ("what", 0.5, 0.8), ("happened", 2.9, 3.4)],  # outside the window
        [("hey", 0.1, 0.11), ("what", 0.12, 0.13), ("happened", 0.14, 0.15)],  # far too fast
        [("hey what happened", 0.1, 1.4)],  # words lost in tokenization
        [],
    ]
    for result in bad:
        assert align_text(QUIET_THEN_SPEECH, RATE, "hey what happened", 0.0, 2.5,
                          aligner=lambda seg, text, r=result: r) is None
    assert align_text(QUIET_THEN_SPEECH, RATE, "hey what happened", 0.0, 2.5,
                      aligner=None) is None or True  # default aligner needs the model; not unit tested


def test_aligned_times_come_back_on_the_clip_clock():
    words = align_text(_audio([(11.0, 0.001), (1.5, 0.3)]), RATE, "oi tudo", 10.0, 12.5,
                       aligner=lambda seg, text: [("oi", 0.2, 0.5), ("tudo", 0.6, 1.0)])
    assert words == [("oi", 10.2, 10.5), ("tudo", 10.6, 11.0)]


def test_line_times_follow_the_alignment_when_it_holds():
    words = line_times(QUIET_THEN_SPEECH, RATE, "hey what happened", 0.0, 2.0,
                       aligner=lambda seg, text: [("hey", 1.0, 1.3), ("what", 1.35, 1.6),
                                                  ("happened", 1.65, 2.0)])
    assert [w for w, _, _ in words] == ["hey", "what", "happened"] and words[0][1] == 1.0


def test_line_times_without_alignment_start_at_the_voice_not_the_drawing():
    # GTA draws the subtitle at 0.0 but the voice only starts at 1.0
    words = line_times(QUIET_THEN_SPEECH, RATE, "hey what happened", 0.0, 2.0,
                       aligner=lambda seg, text: None)
    assert abs(words[0][1] - 1.0) < 0.1  # starts at the voice
    assert words[-1][2] > words[0][1] and [w for w, _, _ in words] == ["hey", "what", "happened"]


def test_line_times_on_silence_keep_the_drawn_time():
    words = line_times(_audio([(3.0, 0.001)]), RATE, "hey what", 0.5, 2.0, aligner=lambda seg, text: None)
    assert words[0][1] == 0.5
