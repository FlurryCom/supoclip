import json
import shutil
import subprocess
from pathlib import Path

import pytest

from supoclip_live import cta

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def probe(path: Path) -> dict:
    return json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout)


def make_clip(path: Path, seconds: float, size="1080x1920", audio=True) -> Path:
    command = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=blue:s={size}:d={seconds}"]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=d={seconds}", "-c:a", "aac"]
    subprocess.run([*command, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)], check=True)
    return path


def test_cta_file_for_unknown_provider_is_none():
    assert cta.cta_file("catclips", "linkedin") is None
    assert cta.cta_file("none", "tiktok") is None
    assert cta.cta_file(None, "tiktok") is None
    assert cta.cta_file("catclips", "instagram-standalone").name == "instagram.mov"
    assert cta.cta_file("catclips", "youtube").exists()


def test_cta_args_overlay_the_tail(tmp_path):
    args = cta.cta_args(tmp_path / "in.mp4", tmp_path / "cta.mov", tmp_path / "out.mp4", 70.0, True)
    graph = args[args.index("-filter_complex") + 1]
    assert "-itsoffset" in args and args[args.index("-itsoffset") + 1] == "67.5"
    assert "overlay" in graph and "gte(t,67.5)" in graph
    assert "amix" in graph and "duration=first" in graph and "adelay=67500" in graph


@needs_ffmpeg
def test_add_cta_scales_to_the_clip(tmp_path):
    clip = make_clip(tmp_path / "clip.mp4", 5, size="720x1280")
    out = cta.with_cta(clip, "catclips", "tiktok")
    assert out != clip and out.exists()
    info = probe(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (720, 1280)
    assert abs(float(info["format"]["duration"]) - 5) < 0.1


@needs_ffmpeg
def test_add_cta_on_silent_clip(tmp_path):
    clip = make_clip(tmp_path / "clip.mp4", 4, audio=False)
    out = cta.with_cta(clip, "catclips", "youtube")
    assert out != clip and any(s["codec_type"] == "audio" for s in probe(out)["streams"])


@needs_ffmpeg
def test_add_cta_falls_back_on_short_or_broken_clip(tmp_path):
    short = make_clip(tmp_path / "short.mp4", 1)
    assert cta.with_cta(short, "catclips", "tiktok") == short
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert cta.with_cta(broken, "catclips", "tiktok") == broken
    assert broken.read_bytes() == b"not a video"


def test_no_cta_for_none_kit_returns_the_clip(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    assert cta.with_cta(clip, "none", "tiktok") == clip


def test_catclips_v2_has_its_own_ctas_over_the_title_bar():
    from supoclip_live.cta import cta_file
    for provider in ("tiktok", "youtube", "instagram"):
        assert cta_file("catclips_v2", provider).parent.name == "catclips_v2"


def _clip(path, with_bar):
    import subprocess
    draw = ",drawbox=x=0:y=720:w=1080:h=200:color=0xFEC803:t=fill" if with_bar else ""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=1080x1920:r=10:d=3",
                    "-vf", "format=yuv420p" + draw, str(path)], check=True)
    return path


def test_v2_cta_only_when_the_clip_has_the_title_bar(tmp_path):
    from supoclip_live import cta
    split = _clip(tmp_path / "split.mp4", True)
    plain = _clip(tmp_path / "plain.mp4", False)
    assert cta.has_title_bar(split) and not cta.has_title_bar(plain)
    assert cta.cta_for(split, "catclips_v2", "tiktok").parent.name == "catclips_v2"
    # A vertical remake or a fallback layout has no bar: the card goes low, in the caption band.
    assert cta.cta_for(plain, "catclips_v2", "tiktok").parent.name == "catclips"
    assert cta.cta_for(plain, "catclips", "tiktok").parent.name == "catclips"
