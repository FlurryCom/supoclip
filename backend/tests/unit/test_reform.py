import subprocess

import pytest

from src.reform import (
    clean_title,
    listing_items,
    loudest_second,
    normalize_source,
    reform_segment,
    valid_video_id,
    video_url,
)


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/@MrBeast", ("youtube", "https://www.youtube.com/@MrBeast/shorts", "@MrBeast")),
    ("youtube.com/@MrBeast/videos", ("youtube", "https://www.youtube.com/@MrBeast/shorts", "@MrBeast")),
    ("https://www.tiktok.com/@khaby.lame?lang=pt", ("tiktok", "https://www.tiktok.com/@khaby.lame", "khaby.lame")),
    ("https://twitch.tv/xqc", ("twitch", "https://www.twitch.tv/xqc/clips?filter=clips&range=30d", "xqc")),
])
def test_normalize_source(url, expected):
    assert normalize_source(url) == expected


@pytest.mark.parametrize("url", [
    "https://evil.com/@x", "javascript:alert(1)", "https://user:pw@youtube.com/@x",
    "https://www.youtube.com/watch?v=abc", "", "https://www.tiktok.com/",
])
def test_normalize_source_rejects_other_hosts(url):
    with pytest.raises(ValueError):
        normalize_source(url)


def test_instagram_is_named():
    with pytest.raises(ValueError, match="instagram"):
        normalize_source("https://www.instagram.com/khaby00/reels/")


def test_listing_items_twitch_uses_the_slug():
    info = {"entries": [{"id": "4192171452", "url": "https://clips.twitch.tv/BraveSlug-abc_1", "title": "t", "duration": 33.0}]}
    assert listing_items(info, "twitch")[0]["id"] == "BraveSlug-abc_1"


def test_listing_items_keeps_title_duration_thumbnail():
    info = {"entries": [
        {"id": "sHpEqeSByPI", "title": "Unboxing", "duration": None, "thumbnails": [{"url": "a"}, {"url": "b"}]},
        None,
    ]}
    assert listing_items(info, "youtube") == [{"id": "sHpEqeSByPI", "title": "Unboxing", "duration": None, "thumbnail": "b"}]


def test_video_url_and_ids():
    assert video_url("tiktok", "7709194461913419038", "khaby.lame") == "https://www.tiktok.com/@khaby.lame/video/7709194461913419038"
    assert video_url("youtube", "sHpEqeSByPI", "@MrBeast") == "https://www.youtube.com/shorts/sHpEqeSByPI"
    assert video_url("twitch", "BraveSlug-abc_1", "xqc") == "https://clips.twitch.tv/BraveSlug-abc_1"
    assert valid_video_id("youtube", "sHpEqeSByPI")
    assert not valid_video_id("youtube", "../etc/passwd")
    assert not valid_video_id("tiktok", "abc")
    assert not valid_video_id("instagram", "x")


def test_clean_title():
    assert clean_title("Coach, trust me 👀   #learnfromkhaby #comedy") == "Coach, trust me 👀"
    assert len(clean_title("x" * 300)) == 100


def test_reform_segment_covers_the_whole_video():
    s = reform_segment(150.4, "Título", "catclips", 12.0)
    assert (s["start_time"], s["end_time"], s["max_seconds"]) == ("00:00", "02:30", 150.4)
    assert s["edit_kit"] == {"id": "catclips", "peak_s": 12.0, "chat": []}
    assert s["sensitive_check"] is True
    assert s["keep_ranges"] == [[0.0, 150.4]]  # fixed ranges: pause cleanup and sentence snapping don't touch a ready cut
    assert s["hook_title"] == "Título"
    assert s["workflow_meta"]["burn_hook_title"] is False


def test_loudest_second_finds_the_loud_part(tmp_path):
    path = tmp_path / "tone.wav"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:d=4",
        "-f", "lavfi", "-i", "sine=f=440:r=8000:d=1",
        "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:d=2",
        "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", str(path),
    ], check=True)
    assert 3.5 <= loudest_second(path) <= 5.0


def test_reform_segment_keeps_full_length_through_render_ranges(tmp_path):
    from src.media.render_ranges import resolve_render_ranges
    video = tmp_path / "long.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=16x16:r=1:d=150",
        "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "150", "-shortest", str(video),
    ], check=True)
    cleanup = {"enabled": True, "remove_pauses": True, "remove_filler_words": True}
    segment = reform_segment(150.0, "t", "catclips", 3.0)
    # task_service caps a segment at its own max_seconds when it has one
    resolved = resolve_render_ranges(video, segment, cleanup, float(segment["max_seconds"]))
    assert resolved.keep_ranges == [(0.0, 150.0)]
