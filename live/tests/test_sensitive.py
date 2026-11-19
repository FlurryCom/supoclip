import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from supoclip_live import cta
from supoclip_live.sensitive import blur_chain, blurred_share, decide

SINCE = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
REGION = {"kind": "nudity", "start": 1.0, "end": 2.0, "x": 0.25, "y": 0.25, "w": 0.5, "h": 0.25}


def row(**over):
    base = {"target_blur": True, "channel_blur": True, "checked_duration": 60.0, "regions": [REGION],
            "check_error": None, "clip_created": SINCE + timedelta(hours=1)}
    base.update(over)
    return base


def test_both_switches_needed_to_blur():
    assert decide(row(target_blur=False), SINCE, 60.0) == ("post", None, [])
    assert decide(row(channel_blur=False), SINCE, 60.0) == ("post", None, [])
    assert decide(row(), SINCE, 60.0) == ("blur", None, [REGION])


def test_clean_clip_just_posts():
    assert decide(row(regions=[]), SINCE, 60.0) == ("post", None, [])


def test_unchecked_clip_is_refused_when_blur_required():
    assert decide(row(checked_duration=None, regions=None), SINCE, 60.0) == ("refuse", "clipe não analisado", [])
    assert decide(row(check_error="gore check failed: x"), SINCE, 60.0) == ("refuse", "análise falhou", [])


def test_old_clips_are_grandfathered():
    old = row(checked_duration=None, regions=None, clip_created=SINCE - timedelta(minutes=1))
    assert decide(old, SINCE, 60.0) == ("post", None, [])


def test_duration_mismatch_refuses():
    assert decide(row(), SINCE, 61.0) == ("refuse", "clipe alterado depois da análise", [])


def test_blurred_share_uses_the_union_of_seconds():
    regions = [dict(REGION, start=0.0, end=10.0), dict(REGION, start=5.0, end=15.0)]
    assert blurred_share(regions, 50.0) == pytest.approx(0.3)


def test_over_40_percent_is_refused():
    heavy = [dict(REGION, start=0.0, end=25.0)]
    assert decide(row(regions=heavy), SINCE, 60.0) == ("refuse", "mais de 40% borrado", [])


def test_blur_args_clamp_regions_to_the_frame():
    edge = dict(REGION, x=0.9, w=0.3, y=0.0, h=0.2)
    chain = ";".join(blur_chain([edge], 1080, 1920, "[in]", "[out]"))
    assert "crop=108:384:972:0" in chain  # 1080 - 972 = 108 px wide, even sizes, ends at the edge
    assert "enable='between(t,1,2)'" in chain


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def make_clip(path: Path, seconds=4, audio=True) -> Path:
    command = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s=720x1280:d={seconds}"]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=d={seconds}", "-c:a", "aac"]
    subprocess.run([*command, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)], check=True)
    return path


def region_stddev(path: Path, at: float) -> float:
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(at), "-i", str(path), "-frames:v", "1",
                          "-vf", "crop=360:320:180:320", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    # Fine detail: mean second difference along rows (edges and texture score high; flat colour and the
    # smooth ramps a blur leaves behind score near zero).
    return sum(abs(raw[i + 1] - 2 * raw[i] + raw[i - 1]) for i in range(1, len(raw) - 1) if i % 360 not in (0, 359)) / len(raw)


@needs_ffmpeg
def test_finish_blurs_a_silent_clip(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", audio=False)
    out = cta.finish_clip(clip, "none", "tiktok", [REGION], must_blur=True)
    assert out != clip
    assert region_stddev(out, 1.5) < region_stddev(clip, 1.5) * 0.3  # the region lost its detail
    assert region_stddev(out, 0.5) > region_stddev(clip, 0.5) * 0.7  # but only during its seconds
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)],
                                     capture_output=True, text=True, check=True).stdout)
    assert any(s["codec_type"] == "audio" for s in info["streams"])


@needs_ffmpeg
def test_finish_raises_when_blur_is_required_and_fails(tmp_path):
    broken = tmp_path / "b.mp4"
    broken.write_bytes(b"nope")
    with pytest.raises(cta.BlurFailed):
        cta.finish_clip(broken, "catclips", "tiktok", [REGION], must_blur=True)


@needs_ffmpeg
def test_finish_without_regions_behaves_like_with_cta(tmp_path):
    clip = make_clip(tmp_path / "c.mp4")
    assert cta.finish_clip(clip, "none", "tiktok", [], must_blur=False) == clip
