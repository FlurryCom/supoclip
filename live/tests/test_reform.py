"""Reformular: the recorder submits ready-made clips one at a time, after covering old captions."""
import asyncio
from pathlib import Path

from supoclip_live import reform, service


def test_check_duration_refuses_long_or_unknown_videos():
    reform.check_duration(180.0)
    for bad in (None, 0, 181.0, "x"):
        try:
            reform.check_duration(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} accepted")


def test_submit_query_skips_users_with_a_queue():
    # The queue rule lives in the SELECT, so one busy user can't hold up the others.
    sql = reform.NEXT_PENDING_SQL
    assert "t.status IN ('pending', 'queued')" in sql and "o.status = 'submitted'" in sql


def test_band_filter_only_for_vertical():
    assert reform.band_filter(1920, 1080) is None
    assert reform.band_filter(1080, 1080) is None
    # 66 % and 22 % of 1920, rounded down to even pixels
    assert reform.band_filter(1080, 1920) == "drawbox=x=0:y=1266:w=iw:h=422:color=black@1:t=fill"


ITEM = {"id": "r1", "user_id": "u1", "platform": "tiktok", "video_id": "7709194461913419038",
        "source_url": "https://www.tiktok.com/@khaby.lame/video/7709194461913419038",
        "source_channel": "khaby.lame", "title": "Coach #comedy"}


class FakeBackend:
    def __init__(self):
        self.uploads = []
        self.tasks = []

    async def upload(self, user_id, path, key):
        self.uploads.append((user_id, Path(path).name, key))
        return "upload://x.mp4"

    async def create_task(self, user_id, video_path, title, key, clip_length, instructions=None):
        self.tasks.append((user_id, video_path, title, key, clip_length))
        return "task-1"


def run_submit(monkeypatch, tmp_path, download):
    monkeypatch.setattr(service, "DATA_DIR", tmp_path)
    monkeypatch.setattr(reform, "download", download)
    backend = FakeBackend()
    live = service.LiveService("", backend)
    updates = []

    async def fetch(sql, params=()):
        if "WHERE ri.status = 'pending'" in sql:
            return [dict(ITEM)]
        if "count(*) AS n" in sql:
            return [{"n": 0}]
        if sql.startswith("UPDATE reform_items"):
            updates.append((sql, params))
        return []

    live.fetch = fetch
    asyncio.run(reform.submit_next(live))
    return backend, updates


def test_failed_download_marks_error(monkeypatch, tmp_path):
    def broken(url, dest):
        raise RuntimeError("Video unavailable")

    backend, updates = run_submit(monkeypatch, tmp_path, broken)
    assert backend.uploads == [] and backend.tasks == []
    assert len(updates) == 1 and "status = 'error'" in updates[0][0] and "Video unavailable" in updates[0][1][0]
    assert list(tmp_path.iterdir()) == []  # temporary files removed


def test_horizontal_clip_is_sent_as_is(monkeypatch, tmp_path):
    def fake_download(url, dest):
        Path(dest).write_bytes(b"video")
        return {"width": 1920, "height": 1080, "duration": 30.0}

    backend, updates = run_submit(monkeypatch, tmp_path, fake_download)
    assert backend.uploads == [("u1", "source.mp4", "reform-upload-r1")]
    assert backend.tasks == [("u1", "upload://x.mp4", "Coach", "reform-task-r1", "auto")]
    assert "status = 'submitted'" in updates[0][0] and updates[0][1] == ("task-1", "r1")
    assert list(tmp_path.iterdir()) == []
