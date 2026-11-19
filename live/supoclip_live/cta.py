"""Edit kits (fork feature): each platform's follow CTA over the last seconds of a clip.

The clip in SupoClip stays clean; right before uploading to a platform the
recorder lays that platform's animation (alpha .mov with its own sounds) over
the tail. Anything going wrong posts the plain clip instead.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

CTA_DIR = Path(__file__).resolve().parents[1] / "assets" / "cta"
CTA_SECONDS = 2.5  # keep in step with KITS[...]["cta_seconds"] in the backend
PROVIDER_FILES = {"tiktok": "tiktok", "youtube": "youtube", "instagram": "instagram", "instagram-standalone": "instagram"}


def cta_file(kit: str | None, provider: str) -> Path | None:
    name = PROVIDER_FILES.get(provider)
    if not kit or kit == "none" or not name:
        return None
    path = CTA_DIR / kit / f"{name}.mov"
    return path if path.exists() else None


def _num(value: float) -> str:
    return f"{round(value, 3):g}"


def cta_args(clip: Path, cta: Path, out: Path, duration: float, has_audio: bool) -> list[str]:
    start = duration - CTA_SECONDS
    graph = [
        "[1:v][0:v]scale2ref=w=iw:h=ih[cta][base]",
        f"[base][cta]overlay=0:0:enable='gte(t,{_num(start)})':eof_action=pass[v]",
        f"[1:a]adelay={int(start * 1000)}:all=1[ctaa]",
        "[0:a]anull[a0]" if has_audio else f"aevalsrc=0:d={_num(duration)}:s=48000[a0]",
        "[a0][ctaa]amix=inputs=2:normalize=0:duration=first[a]",
    ]
    return [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(clip), "-itsoffset", _num(start), "-i", str(cta),
        "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]", "-t", _num(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out),
    ]


def _probe(path: Path) -> tuple[float, bool, tuple[int, int]]:
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    return float(info["format"]["duration"]), has_audio, (video["width"], video["height"])


def with_cta(clip: Path, kit: str | None, provider: str) -> Path:
    """A copy of ``clip`` with the platform's CTA on its tail, or ``clip`` itself when there is none."""
    cta = cta_file(kit, provider)
    if cta is None:
        return clip
    out = clip.with_name(f"{clip.stem}.{provider}.mp4")
    try:
        duration, has_audio, size = _probe(clip)
        if duration < CTA_SECONDS + 0.5:
            return clip
        subprocess.run(cta_args(clip, cta, out, duration, has_audio), check=True, capture_output=True, timeout=600)
        if _probe(out)[2] != size:
            raise RuntimeError("CTA render changed the clip size")
        return out
    except Exception as exc:
        logger.warning("posting %s without its %s CTA: %s", clip.name, provider, exc)
        out.unlink(missing_ok=True)
        return clip
