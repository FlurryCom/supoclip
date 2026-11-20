import pytest
from datetime import datetime, timedelta, timezone

from src.live_scoring import final_score, keep_segments

T0 = datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


CHUNKS = [(T0, 600.0)]  # one 10-minute window file starting at T0
# Quiet chat, then an explosion at 300-310 s on the broadcast clock.
BUCKETS = [(at(10 * i), 90 if i == 30 else 5) for i in range(60)]


def segment(start, end, ai):
    return {"start_time": start, "end_time": end, "virality_score": ai}


def test_mediocre_ai_score_with_exploding_chat_is_kept():
    hot = segment("04:10", "05:10", 62)  # 250-310 s holds the 300 s spike
    assert keep_segments([hot], CHUNKS, BUCKETS, min_score=80, chat_weight=60) == [hot]


def test_good_ai_score_with_dead_chat_is_dropped():
    quiet = segment("00:30", "01:40", 75)
    assert keep_segments([quiet], CHUNKS, BUCKETS, min_score=80, chat_weight=60) == []


def test_without_chat_data_the_ai_score_decides():
    strong, weak = segment("00:00", "01:10", 85), segment("02:00", "03:10", 70)
    assert keep_segments([weak, strong], CHUNKS, [], min_score=80, chat_weight=60) == [strong]


def test_kept_segments_are_best_first():
    a, b = segment("00:00", "01:10", 90), segment("04:10", "05:10", 95)
    kept = keep_segments([a, b], CHUNKS, BUCKETS, min_score=80, chat_weight=60)
    assert kept[0] is b


def test_final_score_matches_the_recorder():
    assert final_score(58, 95, 60) == 80
    assert final_score(70, None, 60) == 70


def with_messages(peak_messages, neighbour_messages=()):
    out = []
    for at, count in BUCKETS:
        i = int((at - T0).total_seconds() // 10)
        msgs = peak_messages if i == 30 else list(neighbour_messages) if i in (29, 31) else []
        out.append((at, count, msgs))
    return out


def test_peak_overlay_picks_hottest_bucket_and_shifts_for_lag():
    from src.live_scoring import peak_overlay
    overlay = peak_overlay(segment("04:10", "05:10", 62), CHUNKS, with_messages(["a", "b"]))
    assert overlay == {"peak_s": 42.0, "chat": ["a", "b"]}


def test_peak_overlay_takes_neighbours_up_to_eight():
    from src.live_scoring import peak_overlay
    peak = [f"p{i}" for i in range(6)]
    overlay = peak_overlay(segment("04:10", "05:10", 62), CHUNKS, with_messages(peak, ["n1", "n2", "n3", "n4"]))
    assert overlay["chat"][:6] == peak and len(overlay["chat"]) == 8


def test_gate_without_messages_attaches_kit_without_chat():
    from src.live_scoring import peak_overlay, segment_kit
    no_text = [(at, count, []) for at, count in BUCKETS]
    overlay = peak_overlay(segment("04:10", "05:10", 62), CHUNKS, no_text)
    assert overlay == {"peak_s": 42.0, "chat": []}  # the hit sound still lands on the peak
    assert peak_overlay(segment("04:10", "05:10", 62), CHUNKS, []) is None
    assert segment_kit("catclips", None) == {"id": "catclips", "peak_s": None, "chat": []}
    assert segment_kit("catclips", overlay) == {"id": "catclips", "peak_s": 42.0, "chat": []}


def test_peak_overlay_never_before_clip_start():
    from src.live_scoring import peak_overlay
    overlay = peak_overlay(segment("04:58", "06:10", 62), CHUNKS, with_messages(["a"]))
    assert overlay["peak_s"] == 0.5


class _Rows:
    def __init__(self, first=None, rows=()):
        self._first, self._rows = first, list(rows)

    def first(self):
        return self._first

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, window, buckets):
        self.window, self.buckets = window, buckets

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        sql = str(query)
        return _Rows(first=self.window) if "FROM live_windows" in sql else _Rows(rows=self.buckets)


@pytest.mark.parametrize("blur", [True, False])
def test_gate_marks_segments_for_the_sensitive_check_only_when_on(monkeypatch, blur):
    import asyncio
    from types import SimpleNamespace
    from src import live_scoring

    window = SimpleNamespace(chunks=[{"start": T0.isoformat(), "duration": 600.0}], recording_id="r1",
                             min_score=80, chat_weight=60, edit_kit="none", blur_sensitive=blur,
                             account_min=None)
    buckets = [SimpleNamespace(at=at, chatters=c, messages=[]) for at, c in BUCKETS]
    monkeypatch.setattr(live_scoring, "get_session_maker", lambda: (lambda: _FakeDb(window, buckets)))
    kept = asyncio.run(live_scoring.gate_live_segments("t1", [segment("04:10", "05:10", 62)]))
    assert len(kept) == 1
    assert kept[0].get("sensitive_check", False) is blur


def test_processing_threshold_follows_the_posting_accounts():
    from src.live_scoring import processing_threshold
    assert processing_threshold(channel_min=60, account_min=80) == 80  # our accounts' rule wins
    assert processing_threshold(channel_min=90, account_min=80) == 80
    assert processing_threshold(channel_min=70, account_min=None) == 70  # no active account: the channel's rule
