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
