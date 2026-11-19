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

from .sensitive import blur_chain

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


def cta_args(
    clip: Path, cta: Path | None, out: Path, duration: float, has_audio: bool,
    regions: list | None = None, size: tuple[int, int] = (0, 0),
) -> list[str]:
    """One ffmpeg run: blur the sensitive regions, then lay the CTA (if any) over the tail."""
    graph: list[str] = []
    video = "[0:v]"
    if regions:
        graph += blur_chain(regions, size[0], size[1], "[0:v]", "[blurred]")
        video = "[blurred]"
    base_audio = "[0:a]anull[a0]" if has_audio else f"aevalsrc=0:d={_num(duration)}:s=48000[a0]"
    inputs = ["-i", str(clip)]
    if cta is not None:
        start = duration - CTA_SECONDS
        inputs += ["-itsoffset", _num(start), "-i", str(cta)]
        graph += [
            f"[1:v]{video}scale2ref=w=iw:h=ih[cta][base]",
            f"[base][cta]overlay=0:0:enable='gte(t,{_num(start)})':eof_action=pass[v]",
            f"[1:a]adelay={int(start * 1000)}:all=1[ctaa]",
            base_audio,
            "[a0][ctaa]amix=inputs=2:normalize=0:duration=first[a]",
        ]
    else:
        graph += [f"{video}null[v]", base_audio, "[a0]anull[a]"]
    return [
        "ffmpeg", "-y", "-loglevel", "error", *inputs,
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


class BlurFailed(RuntimeError):
    """The clip had to be blurred for this account and couldn't be: don't post it."""


def finish_clip(clip: Path, kit: str | None, provider: str, regions: list, must_blur: bool) -> Path:
    """The file to upload: ``clip`` blurred where needed and with the platform's CTA.

    Without anything to add it returns ``clip``. If adding fails it returns
    ``clip`` too, unless the blur was required: then it raises BlurFailed.
    """
    cta = cta_file(kit, provider)
    regions = regions if must_blur else []
    if cta is None and not regions:
        return clip
    out = clip.with_name(f"{clip.stem}.{provider}.mp4")
    try:
        duration, has_audio, size = _probe(clip)
        if cta is not None and duration < CTA_SECONDS + 0.5:
            cta = None
            if not regions:
                return clip
        subprocess.run(cta_args(clip, cta, out, duration, has_audio, regions, size), check=True, capture_output=True, timeout=600)
        if _probe(out)[2] != size:
            raise RuntimeError("render changed the clip size")
        return out
    except Exception as exc:
        out.unlink(missing_ok=True)
        if regions:
            raise BlurFailed(str(exc)[:300]) from exc
        logger.warning("posting %s without its %s CTA: %s", clip.name, provider, exc)
        return clip


def with_cta(clip: Path, kit: str | None, provider: str) -> Path:
    """A copy of ``clip`` with the platform's CTA on its tail, or ``clip`` itself when there is none."""
    return finish_clip(clip, kit, provider, [], must_blur=False)
