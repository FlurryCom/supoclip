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


# Cat Clips v2's yellow title bar (rows 720-920 of 1920) and the kit to fall back on without it.
BAR_ROWS, BAR_RGB = (720, 920), (254, 200, 3)
BAR_FALLBACK = {"catclips_v2": "catclips"}


def has_title_bar(clip: Path) -> bool:
    """Whether the clip shows the v2 title bar (a vertical remake or a fallback layout doesn't).

    The title covers most of the bar, so only the thin yellow strips above and below the
    text and the two side margins are checked, on a 108x192 sample of one frame.
    """
    w, h = 108, 192
    try:
        raw = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-ss", "1", "-i", str(clip), "-frames:v", "1",
             "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True, timeout=60,
        ).stdout
    except Exception:
        return False
    if len(raw) != w * h * 3:
        return False
    top, bottom = BAR_ROWS[0] * h // 1920, BAR_ROWS[1] * h // 1920  # rows 72..92

    def pixel(x: int, y: int) -> bytes:
        i = (y * w + x) * 3
        return raw[i:i + 3]

    strips = [pixel(x, y) for y in (top + 1, bottom - 2) for x in range(w)]
    margins = [pixel(x, y) for y in range(top + 1, bottom - 1) for x in (0, 1, w - 2, w - 1)]
    sample = strips + margins
    yellow = sum(1 for p in sample if all(abs(p[c] - BAR_RGB[c]) < 45 for c in range(3)))
    return yellow > 0.75 * len(sample)


def cta_for(clip: Path, kit: str | None, provider: str) -> Path | None:
    """The CTA that fits this clip's layout: v2's sits on the title bar, so without the bar use the low one."""
    if kit in BAR_FALLBACK and not has_title_bar(clip):
        kit = BAR_FALLBACK[kit]
    return cta_file(kit, provider)


def finish_clip(clip: Path, kit: str | None, provider: str, regions: list, must_blur: bool) -> Path:
    """The file to upload: ``clip`` blurred where needed and with the platform's CTA.

    Without anything to add it returns ``clip``. If adding fails it returns
    ``clip`` too, unless the blur was required: then it raises BlurFailed.
    """
    cta = cta_for(clip, kit, provider)
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
