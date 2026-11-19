import shutil
import subprocess

import numpy as np
import pytest

from src.media.sensitive_check import analyze_clip, sample_frames

NUDE = {"kind": "nudity", "start": 1.0, "end": 1.5, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}
GORE = {"kind": "gore", "start": 2.0, "end": 3.0, "x": 0.25, "y": 0.25, "w": 0.25, "h": 0.25}


def make_clip(path, seconds=3):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s=320x568:d={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@needs_ffmpeg
def test_sample_frames_reads_two_per_second(tmp_path):
    frames = sample_frames(make_clip(tmp_path / "c.mp4"), fps=2)
    assert [t for t, _ in frames] == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
    assert frames[0][1].shape[1] == 640 and frames[0][1].dtype == np.uint8


@needs_ffmpeg
def test_analyze_clip_merges_both_checks(tmp_path):
    clip = make_clip(tmp_path / "c.mp4")
    result = analyze_clip(clip, find_nudity=lambda frames: [NUDE], find_gore=lambda frames: [GORE])
    assert result["regions"] == [NUDE, GORE] and result["error"] is None
    assert abs(result["duration"] - 3.0) < 0.1


@needs_ffmpeg
def test_analyze_clip_keeps_nudity_when_gore_fails(tmp_path):
    clip = make_clip(tmp_path / "c.mp4")

    def broken(frames):
        raise RuntimeError("cli down")

    result = analyze_clip(clip, find_nudity=lambda frames: [NUDE], find_gore=broken)
    assert result["regions"] == [NUDE]
    assert result["error"].startswith("gore check failed")


def test_analyze_clip_reports_a_broken_file(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"nope")
    result = analyze_clip(bad, find_nudity=lambda f: [], find_gore=lambda f: [])
    assert result["error"] and result["regions"] == []
