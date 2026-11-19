from datetime import datetime, timedelta, timezone

from supoclip_live.heat import chat_heat, chat_hints, final_score, to_file_time
from supoclip_live.windows import Chunk

T0 = datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def stream(counts):
    return [(at(10 * i), c) for i, c in enumerate(counts)]


def test_clip_over_the_hottest_moment_scores_near_100():
    buckets = stream([5] * 60 + [80] + [5] * 59)
    assert chat_heat(buckets, at(590), at(650)) >= 95


def test_ordinary_clip_scores_far_below_the_hot_one():
    buckets = stream([3, 5, 4, 6, 5, 4] * 10 + [80] + [3, 5, 4, 6, 5, 4] * 10)
    ordinary = chat_heat(buckets, at(100), at(160))
    assert ordinary <= 70
    assert chat_heat(buckets, at(570), at(630)) - ordinary >= 25


def test_long_quiet_clip_is_not_inflated_by_its_length():
    # Noise only: a 2-minute stretch must not look hot just by covering many buckets.
    buckets = stream([3, 5, 4, 6, 5, 4] * 20)
    assert chat_heat(buckets, at(0), at(120)) <= 70


def test_heat_counts_the_reaction_after_the_clip_ends():
    buckets = stream([5] * 60 + [5, 90] + [5] * 58)
    # Clip ends at 600 s; the 610 s burst is chat reacting to it.
    assert chat_heat(buckets, at(540), at(600)) >= 95


def test_too_little_chat_gives_no_heat():
    assert chat_heat(stream([5] * 10), at(0), at(60)) is None


def test_final_score_mixes_by_chat_weight():
    assert final_score(58, 95, 60) == 80
    assert final_score(75, 10, 60) == 36
    assert final_score(70, 100, 0) == 70
    assert final_score(70, None, 60) == 70


def test_file_time_skips_ad_gaps():
    chunks = [Chunk(at(0), 60), Chunk(at(150), 60)]  # 90 s ad gap
    assert to_file_time(chunks, at(30)) == 30
    assert to_file_time(chunks, at(100)) is None
    assert to_file_time(chunks, at(160)) == 70


def test_hints_name_the_hot_moments_in_file_time():
    buckets = stream([5] * 30 + [90] + [5] * 29)  # spike at 300 s of wall clock
    chunks = [Chunk(at(240), 120)]  # window file starts at 240 s
    text = chat_hints(buckets, chunks)
    assert text and " 01:00 " in text
    assert len(text) <= 500


def test_hints_fit_the_limit_with_many_peaks():
    buckets = stream(list(range(400)))  # chat keeps growing: the last 15 % are all hot
    text = chat_hints(buckets, [Chunk(at(0), 4000)])
    assert text and len(text) <= 500
