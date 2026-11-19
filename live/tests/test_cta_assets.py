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
@pytest.mark.parametrize("at", [0.2, 0.6, 1.0, 1.15, 1.3, 1.5, 2.0, 2.4])
def test_cta_sits_between_the_video_and_the_captions(provider, at):
    """Owner: in the gap under the video and above our captions, never over either.

    Measured on a real live clip: the video band ends at 1265 px and a one-line
    caption pill starts at 1458 px (a two-line caption reaches about 1410 px).
    """
    assert max(alpha_rows(provider, 0, 1265, at)) == 0  # nothing over the video, at any moment
    assert max(alpha_rows(provider, 1410, 510, at)) == 0  # nothing over the captions or the app's own UI
    assert max(alpha_rows(provider, 1265, 145, at)) > 0  # the card is in the gap
