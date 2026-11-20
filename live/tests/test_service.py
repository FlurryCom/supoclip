from supoclip_live.service import fair_share


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


def test_backlog_older_than_an_hour_is_skipped():
    from datetime import datetime, timedelta, timezone
    from supoclip_live.service import MAX_BACKLOG, skip_backlog
    end = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)
    assert MAX_BACKLOG == timedelta(hours=1)
    assert skip_backlog(end - timedelta(minutes=50), end) is None  # within an hour: keep it
    assert skip_backlog(end - timedelta(hours=6), end) == end - MAX_BACKLOG  # jump to the last hour


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
