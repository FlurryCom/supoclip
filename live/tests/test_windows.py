from datetime import datetime, timedelta, timezone

from supoclip_live.windows import (
    Chunk, decide, is_duplicate, parse_clip_time, to_wall_clock, window_chunks,
)

T0 = datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)


def m(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def run(**overrides):
    args = dict(
        pending_from=T0, content_end=m(10), ended=False, busy=False, chat_filter=False,
        spikes=[], sent_last_day=0, daily_cap=12,
    )
    args.update(overrides)
    return decide(**args)


def test_waits_until_a_step_of_new_content():
    assert run(content_end=m(9.5)).action == "wait"


def test_first_window_submits_everything_recorded():
    d = run(content_end=m(10))
    assert (d.action, d.start, d.end) == ("submit", m(-10), m(10))


def test_later_window_repeats_ten_minutes_of_context():
    d = run(pending_from=m(10), content_end=m(20))
    assert (d.start, d.end) == (m(0), m(20))


def test_busy_channel_waits_and_then_merges_up_to_the_cap():
    assert run(pending_from=m(10), content_end=m(25), busy=True).action == "wait"
    d = run(pending_from=m(10), content_end=m(45))
    assert (d.start, d.end) == (m(5), m(45))  # 40-minute cap drops the oldest 5


def test_chat_filter_skips_quiet_windows():
    d = run(chat_filter=True, spikes=[])
    assert (d.action, d.reason) == ("skip", "no chat spike")


def test_spike_in_overlap_does_not_resend():
    d = run(pending_from=m(10), content_end=m(20), chat_filter=True, spikes=[m(5)])
    assert d.action == "skip"


def test_spike_at_the_edge_waits_for_its_aftermath():
    assert run(chat_filter=True, spikes=[m(9.5)]).action == "wait"
    assert run(chat_filter=True, spikes=[m(9.5)], content_end=m(12)).action == "submit"


def test_stream_end_flushes_a_short_tail_but_not_a_tiny_one():
    assert run(pending_from=m(10), content_end=m(14), ended=True).action == "submit"
    assert run(pending_from=m(10), content_end=m(11), ended=True).action == "wait"


def test_daily_cap_skips():
    d = run(sent_last_day=12)
    assert (d.action, d.reason) == ("skip", "daily window cap reached")


def test_clip_time_maps_across_an_ad_gap():
    chunks = [Chunk(m(0), 60), Chunk(m(1), 30), Chunk(m(3), 60)]  # 90 s of ads after the 2nd
    assert to_wall_clock(chunks, 30) == m(0.5)
    assert to_wall_clock(chunks, 100) == m(3) + timedelta(seconds=10)
    assert to_wall_clock(chunks, 10_000) == m(4)


def test_window_chunks_keeps_partial_overlaps():
    chunks = [Chunk(m(i), 60) for i in range(5)]
    assert window_chunks(chunks, m(1.5), m(3)) == chunks[1:3]


def test_parse_clip_time_formats():
    assert parse_clip_time("01:30") == 90
    assert parse_clip_time("1:02:03") == 3723
    assert parse_clip_time("12.5") == 12.5


def test_duplicate_needs_more_than_half_of_the_shorter_clip():
    kept = [(m(0), m(1))]
    assert is_duplicate((m(0.4), m(1.4)), kept)
    assert not is_duplicate((m(0.6), m(1.6)), kept)
