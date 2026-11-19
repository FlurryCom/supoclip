import shutil
import subprocess
from pathlib import Path

import pytest

from src.edit_kits import get_kit
from src.media.edit_kit_render import apply_edit_kit, chat_events, kit_ffmpeg_args
from src.media.ffmpeg import ffprobe_duration, ffprobe_has_audio, ffprobe_video_size

COLORS = get_kit("catclips")["colors"]


def starts(events):
    def seconds(stamp):
        h, m, s = stamp.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    return [(seconds(e.split(",")[1]), seconds(e.split(",")[2])) for e in events]


def test_chat_events_pop_in_order():
    events = chat_events(["a", "b", "c"], 10.0, 70.0, 1080, 1920, COLORS, 1.5)
    assert [round(s, 2) for s, _ in starts(events)] == [10.0, 10.4, 10.8]
    assert all(round(e - s, 2) == 2.5 for s, e in starts(events))


def test_chat_events_clamped_before_end_card():
    events = chat_events([str(i) for i in range(8)], 69.0, 70.0, 1080, 1920, COLORS, 1.5)
    assert len(events) == 8
    assert all(end <= 70.0 - 1.5 + 1e-6 for _, end in starts(events))


def test_chat_events_without_peak_or_chat_are_empty():
    assert chat_events([], 10.0, 70.0, 1080, 1920, COLORS, 1.5) == []
    assert chat_events(["a"], None, 70.0, 1080, 1920, COLORS, 1.5) == []


def test_chat_text_is_escaped():
    (event,) = chat_events(["{\\b1}oi\\N"], 5.0, 70.0, 1080, 1920, COLORS, 1.5)
    text = event.split(",", 9)[9]
    assert "\\{\\\\b1\\}oi" in text  # braces and backslashes escaped, no live override tag


def test_kit_args_without_chat_still_mix_whoosh_and_card(tmp_path):
    kit = get_kit("catclips")
    args = kit_ffmpeg_args(tmp_path / "in.mp4", tmp_path / "out.mp4", kit, None, None, 70.0, tmp_path, True)
    joined = " ".join(map(str, args))
    graph = args[args.index("-filter_complex") + 1]
    assert str(kit["end_card"]) in joined
    assert "cartoon-whoosh.wav" in joined and "sfx-golpe-abertura.wav" not in joined
    assert "subtitles=" not in graph
    assert "amix" in graph and "overlay" in graph and "gte(t,68.5)" in graph


def make_clip(path: Path, seconds: int = 4) -> None:
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=blue:s=1080x1920:d={seconds}",
        "-f", "lavfi", "-i", f"sine=d={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], check=True)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_apply_edit_kit_smoke(tmp_path):
    clip = tmp_path / "clip.mp4"
    make_clip(clip)
    before = ffprobe_duration(clip)
    assert apply_edit_kit(clip, {"id": "catclips", "peak_s": 1.0, "chat": ["oi", "KKKK"]}) is True
    assert abs(ffprobe_duration(clip) - before) < 0.1
    assert ffprobe_video_size(clip) == (1080, 1920)
    assert ffprobe_has_audio(clip)


def test_apply_edit_kit_failure_keeps_clip(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"not a video")
    assert apply_edit_kit(clip, {"id": "catclips", "peak_s": 1.0, "chat": ["oi"]}) is False
    assert clip.read_bytes() == b"not a video"


def test_apply_edit_kit_none_kit_is_a_no_op(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    assert apply_edit_kit(clip, {"id": "none", "peak_s": None, "chat": []}) is True
    assert clip.read_bytes() == b"x"
