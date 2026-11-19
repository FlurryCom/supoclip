import json
import shutil
import subprocess
from pathlib import Path

import pytest

CTA = Path(__file__).resolve().parents[1] / "assets" / "cta" / "catclips"


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="needs ffprobe")
@pytest.mark.parametrize("provider", ["tiktok", "youtube", "instagram"])
def test_each_cta_is_a_short_alpha_movie_with_sound(provider):
    path = CTA / f"{provider}.mov"
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout)
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert video["codec_name"] == "png" and "a" in video["pix_fmt"]
    assert (video["width"], video["height"]) == (1080, 1920)
    assert int(video["nb_read_frames"]) == 75
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    assert abs(float(probe["format"]["duration"]) - 2.5) < 0.05


def alpha_rows(provider: str, top: int, height: int, at: float = 1.5) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", str(at), "-i", str(CTA / f"{provider}.mov"), "-frames:v", "1",
         "-vf", f"crop=1080:{height}:0:{top},format=rgba,alphaextract", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
@pytest.mark.parametrize("provider", ["tiktok", "youtube", "instagram"])
def test_cta_sits_low_and_small_below_the_captions(provider):
    """Owner: the CTA took too much screen; it goes at the bottom, centred, under the captions (to ~1490 px)."""
    assert max(alpha_rows(provider, 0, 1490)) == 0  # nothing over the video or the captions
    assert max(alpha_rows(provider, 1810, 110)) == 0  # clear of the very bottom edge
    assert max(alpha_rows(provider, 1490, 320)) > 0  # the card is there
