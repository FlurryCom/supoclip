"""post_clips must never upload a clip that had to be blurred and wasn't."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from supoclip_live import cta, service

SINCE = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
REGION = {"kind": "nudity", "start": 1.0, "end": 2.0, "x": 0.25, "y": 0.25, "w": 0.5, "h": 0.25}


def candidate(**over):
    row = {
        "clip_id": "c1", "final_score": 90, "task_id": "t1", "hook_title": "hook", "platform": "twitch",
        "channel": "streamer", "edit_kit": "catclips", "live_title": "live", "target_id": "tg1", "user_id": "u1",
        "integration_id": "i1", "provider": "tiktok", "name": "Cat Clips", "mode": "now", "daily_cap": 10,
        "visibility": "private", "post_visibility": "default",
        "target_blur": True, "channel_blur": True, "clip_created": SINCE + timedelta(hours=1),
        "checked_duration": 60.0, "regions": [REGION], "check_error": None, "safe_posting": True,
    }
    row.update(over)
    return row


class FakeBackend:
    async def download_clip(self, user_id, task_id, clip_id, dest: Path):
        dest.write_bytes(b"clip")


class FakePostiz:
    def __init__(self):
        self.uploads = []

    async def integrations(self):
        return [{"id": "i1", "provider": "tiktok", "name": "Cat Clips", "disabled": False}]

    async def upload(self, path):
        self.uploads.append(Path(path).name)
        return {"id": "m1", "path": "https://x/m1.mp4"}

    async def create_post(self, body):
        return "p1"


def run_post(monkeypatch, tmp_path, row, finish):
    monkeypatch.setattr(service, "DATA_DIR", tmp_path)
    monkeypatch.setattr(service, "POSTIZ_ALLOWED_USER_IDS", ["u1"])
    monkeypatch.setattr(cta, "_probe", lambda path: (60.0, True, (720, 1280)))
    monkeypatch.setattr(cta, "finish_clip", finish)
    postiz = FakePostiz()
    live = service.LiveService("", FakeBackend(), postiz)
    recorded = []

    async def fetch(sql, params=()):
        if "FROM live_clips lc" in sql:
            return [row]
        if "live_feature_marks" in sql:
            return [{"at": SINCE}]
        if "count(*) AS n" in sql:
            return [{"n": 0, "last_posted": None}]
        if sql.startswith("INSERT INTO live_posts"):
            recorded.append((params[3], params[5]))
        return []

    live.fetch = fetch
    asyncio.run(live.post_clips())
    return postiz.uploads, recorded


def test_refused_clip_is_never_uploaded(monkeypatch, tmp_path):
    calls = []
    uploads, recorded = run_post(monkeypatch, tmp_path, candidate(checked_duration=None, regions=None),
                                 lambda *a: calls.append(a) or a[0])
    assert uploads == [] and calls == []
    assert recorded == [("refused", "clipe não analisado")]


def test_failed_blur_is_never_uploaded(monkeypatch, tmp_path):
    def failing(*args):
        raise cta.BlurFailed("ffmpeg died")

    uploads, recorded = run_post(monkeypatch, tmp_path, candidate(), failing)
    assert uploads == [] and recorded == [("refused", "falha ao borrar")]


def test_blur_is_required_only_when_both_switches_are_on(monkeypatch, tmp_path):
    seen = []

    def finish(clip, kit, provider, regions, must_blur):
        seen.append((regions, must_blur))
        return clip

    run_post(monkeypatch, tmp_path, candidate(), finish)
    run_post(monkeypatch, tmp_path, candidate(target_blur=False), finish)
    assert seen == [([REGION], True), ([], False)]


def test_safe_posting_waits_15_minutes_between_posts(monkeypatch, tmp_path):
    from supoclip_live.service import SAFE_GAP, safe_to_post
    now = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)
    assert SAFE_GAP == timedelta(minutes=15)
    assert safe_to_post(True, None, now)  # nothing posted yet
    assert not safe_to_post(True, now - timedelta(minutes=14), now)
    assert safe_to_post(True, now - timedelta(minutes=15), now)
    assert safe_to_post(False, now - timedelta(minutes=1), now)  # switch off: no wait


def test_post_clips_skips_an_account_inside_the_gap(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "DATA_DIR", tmp_path)
    monkeypatch.setattr(service, "POSTIZ_ALLOWED_USER_IDS", ["u1"])
    monkeypatch.setattr(cta, "_probe", lambda path: (60.0, True, (720, 1280)))
    monkeypatch.setattr(cta, "finish_clip", lambda clip, *args: clip)
    postiz = FakePostiz()
    live = service.LiveService("", FakeBackend(), postiz)
    row = candidate(safe_posting=True, target_blur=False)

    async def fetch(sql, params=()):
        if "FROM live_clips lc" in sql:
            return [row]
        if "live_feature_marks" in sql:
            return [{"at": SINCE}]
        if "count(*) AS n" in sql:
            return [{"n": 0, "last_posted": datetime.now(timezone.utc) - timedelta(minutes=5)}]
        return []

    live.fetch = fetch
    asyncio.run(live.post_clips())
    assert postiz.uploads == []  # waits; the clip stays in the queue
