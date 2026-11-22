from supoclip_live.service import fair_share, slot_free


def test_one_account_gets_every_slot():
    assert fair_share(3, 1) == 3


def test_slots_are_shared_rounding_up():
    assert fair_share(3, 2) == 2
    assert fair_share(3, 3) == 1


def test_every_account_gets_at_least_one():
    assert fair_share(3, 10) == 1
    assert fair_share(3, 0) == 3


def test_window_cap_follows_the_channel_switch():
    from supoclip_live.service import window_cap
    assert window_cap({"limit_windows": True, "daily_window_cap": 48}) == 48
    assert window_cap({"limit_windows": False, "daily_window_cap": 48}) > 10_000  # off: no daily limit


def test_backlog_older_than_20_minutes_is_skipped():
    from datetime import datetime, timedelta, timezone
    from supoclip_live.service import MAX_BACKLOG, skip_backlog
    end = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)
    # Owner (2026-11-20, disk full again): at most 20 minutes of unprocessed live per channel.
    assert MAX_BACKLOG == timedelta(minutes=20)
    assert skip_backlog(end - timedelta(minutes=15), end) is None  # within 20 min: keep it
    assert skip_backlog(end - timedelta(minutes=50), end) == end - MAX_BACKLOG  # jump to the last 20 min


def test_recordings_stop_when_the_disk_is_almost_full(monkeypatch):
    import asyncio
    from collections import namedtuple
    from supoclip_live import service
    stopped = []
    live = service.LiveService("", None)
    live.sessions = {"a": object(), "b": object()}

    async def end(channel_id):
        stopped.append(channel_id)
        live.sessions.pop(channel_id)

    live.end = end
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(service.shutil, "disk_usage", lambda path: usage(0, 0, 6 * 2**30))
    asyncio.run(live.stop_if_disk_low())
    assert stopped == []  # 6 GB free: above the 5 GB stop line
    monkeypatch.setattr(service.shutil, "disk_usage", lambda path: usage(0, 0, 4 * 2**30))
    asyncio.run(live.stop_if_disk_low())
    assert sorted(stopped) == ["a", "b"]


def _watch(monkeypatch, live_info, sessions):
    import asyncio
    from supoclip_live import recorder, service
    sql = []
    live = service.LiveService("", None)
    live.sessions = sessions

    async def fetch(query, params=()):
        sql.append(query)
        return []

    async def probe(channel, platform):
        return live_info

    live.fetch = fetch
    monkeypatch.setattr(recorder, "probe", probe)
    asyncio.run(live.watch({"id": "c1", "channel": "xqc", "platform": "twitch", "user_id": "u1"}))
    return " ".join(sql)


def test_a_live_channel_without_a_slot_is_marked_waiting(monkeypatch):
    from supoclip_live import service
    full = {f"s{i}": type("S", (), {"channel": {"user_id": "u1"}, "recording": type("R", (), {"running": True})()})()
            for i in range(service.MAX_RECORDINGS)}
    assert "waiting_since = coalesce(waiting_since, now())" in _watch(monkeypatch, {"title": "GTA"}, full)


def test_an_offline_channel_is_no_longer_waiting(monkeypatch):
    assert "waiting_since = NULL" in _watch(monkeypatch, None, {})


def test_recorder_uses_the_same_processing_threshold():
    from supoclip_live.service import processing_threshold
    assert processing_threshold(60, 80) == 80
    assert processing_threshold(70, None) == 70


def test_slot_free_uses_the_account_number(monkeypatch):
    from supoclip_live import service
    monkeypatch.setattr(service, "MAX_RECORDINGS", 24)
    assert slot_free(total=14, mine=14, account_limit=15, owners=1)
    assert not slot_free(total=15, mine=15, account_limit=15, owners=1)
    assert not slot_free(total=24, mine=3, account_limit=30, owners=1)  # the machine's ceiling still wins


def test_slot_free_defaults_to_ten(monkeypatch):
    from supoclip_live import service
    monkeypatch.setattr(service, "MAX_RECORDINGS", 24)
    assert slot_free(total=9, mine=9, account_limit=None, owners=1)
    assert not slot_free(total=10, mine=10, account_limit=None, owners=1)


def test_slot_free_lower_limit_keeps_running(monkeypatch):
    # Lowering the number never ends a recording: it only decides whether a new one starts.
    from supoclip_live import service
    monkeypatch.setattr(service, "MAX_RECORDINGS", 24)
    assert not slot_free(total=12, mine=12, account_limit=5, owners=1)
    assert not slot_free(total=12, mine=12, account_limit=12, owners=2)  # fair share: 12 of 24 each
