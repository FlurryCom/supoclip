"""Edit kits (fork feature): the extras a kit burns on a finished clip.

One ffmpeg pass on the rendered clip adds real chat popping in at the chat
peak, the kit's sound effects and its end card. The clip's length never
changes, and any failure keeps the clip as it was.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ..edit_kits import get_kit
from .captions import ass_font_name, ass_fonts_dir, ass_timestamp, hex_to_ass_color
from .ffmpeg import ffprobe_duration, ffprobe_has_audio, ffprobe_video_size, run_ffmpeg_command, subtitles_filter_fragment

logger = logging.getLogger(__name__)

BUBBLE_FONT = "GochiHand-Regular"
STEP_S = 0.4  # one message pops in after another
LIFE_S = 2.5  # how long each message stays
MAX_BUBBLE_CHARS = 32  # longer lines run off a 9:16 frame at this font size


def _num(value: float) -> str:
    return f"{round(value, 3):g}"


def _bubble_text(text: str) -> str:
    """Chat shown literally: libass has no escape for a backslash, so a word joiner
    after each one stops it pairing with the next letter (\\N, \\o/)."""
    return text.replace("\\", "\\\u2060").replace("{", "\\{").replace("}", "\\}")


def map_to_clip(source_s: float, keep_ranges: list[tuple[float, float]], output_offsets: list[float]) -> float:
    """Where a source-video second plays in the clip, after silence cuts; a cut moment maps to what follows."""
    for (start, end), offset in zip(keep_ranges, output_offsets):
        if source_s < start:
            return offset
        if source_s <= end:
            return offset + source_s - start
    (start, end), offset = keep_ranges[-1], output_offsets[-1]
    return offset + end - start


def chat_events(
    chat: list[str],
    peak_s: float | None,
    duration: float,
    width: int,
    height: int,
    colors: dict[str, str],
    end_card_seconds: float,
) -> list[str]:
    """ASS dialogue lines: each message pops in on the left, one row each, before the end card."""
    chat = [line for line in chat if len(line) <= MAX_BUBBLE_CHARS]
    if not chat or peak_s is None:
        return []
    last_end = duration - end_card_seconds
    first = max(0.0, min(peak_s, last_end - STEP_S * (len(chat) - 1) - LIFE_S))
    font_px = round(height * 0.03)
    row = round(font_px * 1.6)
    x, bottom = round(width * 0.06), round(height * 0.55)
    events = []
    for i, text in enumerate(chat):
        start = first + STEP_S * i
        end = min(start + LIFE_S, last_end)
        y = bottom - (len(chat) - 1 - i) * row
        pop = f"{{\\an7\\pos({x},{y})\\fscx80\\fscy80\\t(0,120,\\fscx100\\fscy100)}}"
        events.append(f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},Bubble,,0,0,0,,{pop}{_bubble_text(text)}")
    return events


def chat_ass(events: list[str], width: int, height: int, colors: dict[str, str]) -> str:
    font_px = round(height * 0.03)
    cream = hex_to_ass_color(colors.get("cream"), "#F3E9D2")
    black = hex_to_ass_color(colors.get("black"), "#111111")
    yellow = hex_to_ass_color(colors.get("yellow"), "#FEC803")
    # BorderStyle 3: an opaque box in OutlineColour (black), its shadow in BackColour (yellow).
    style = (
        f"Style: Bubble,{ass_font_name(BUBBLE_FONT)},{font_px},{cream},{cream},{black},{yellow},"
        "0,0,0,0,100,100,0,0,3,10,6,7,0,0,0,1"
    )
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 2", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        style, "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events, "",
    ])


def kit_ffmpeg_args(
    clip: Path,
    out: Path,
    kit: dict[str, Any],
    ass_path: Path | None,
    peak_s: float | None,
    duration: float,
    fonts_dir: Path | None,
    has_audio: bool,
) -> list[str]:
    """One ffmpeg command: chat subtitles, end card overlay, sound effects mixed under the clip audio."""
    card_at = duration - kit["end_card_seconds"]
    inputs = ["-i", str(clip), "-loop", "1", "-t", _num(duration), "-i", str(kit["end_card"])]
    sounds = []
    for sfx in kit["sfx"]:
        at = 0.0 if sfx["at"] == "start" else peak_s
        if at is None:
            continue
        sounds.append((len(sounds) + 2, at, sfx["volume"]))
        inputs += ["-i", str(sfx["path"])]
    subtitles = f"{subtitles_filter_fragment(ass_path, fonts_dir)}," if ass_path else ""
    parts = [
        f"[0:v]{subtitles}setsar=1[base]",
        "[1:v][base]scale2ref=w=iw:h=ih[cardraw][base2]",
        f"[cardraw]format=rgba,fade=in:st={_num(card_at)}:d=0.3:alpha=1[card]",
        f"[base2][card]overlay=0:0:enable='gte(t,{_num(card_at)})':shortest=1[v]",
        "[0:a]anull[a0]" if has_audio else f"aevalsrc=0:d={_num(duration)}:s=48000[a0]",
    ]
    labels = "[a0]"
    for index, at, volume in sounds:
        ms = int(at * 1000)
        parts.append(f"[{index}:a]adelay={ms}:all=1,volume={volume}[s{index}]")
        labels += f"[s{index}]"
    parts.append(f"{labels}amix=inputs={len(sounds) + 1}:normalize=0:duration=first[a]")
    return [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(parts),
        "-map", "[v]", "-map", "[a]", "-t", _num(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out),
    ]


def apply_edit_kit(clip_path: Path, edit_kit: dict[str, Any]) -> bool:
    """Burn the kit's extras into ``clip_path`` in place; False (clip untouched) if anything fails."""
    kit = get_kit(edit_kit.get("id"))
    if kit["id"] == "none":
        return True
    clip_path = Path(clip_path)
    out = clip_path.with_name(clip_path.stem + ".kit.mp4")
    ass_path = clip_path.with_name(clip_path.stem + ".kit.ass")
    try:
        duration = ffprobe_duration(clip_path)
        width, height = ffprobe_video_size(clip_path)
        if duration <= kit["end_card_seconds"] or not width or not height:
            return False
        peak_s = edit_kit.get("peak_s")
        events = chat_events(edit_kit.get("chat") or [], peak_s, duration, width, height, kit["colors"], kit["end_card_seconds"]) if kit["chat_overlay"] else []
        if events:
            ass_path.write_text(chat_ass(events, width, height, kit["colors"]), encoding="utf-8")
        command = kit_ffmpeg_args(
            clip_path, out, kit, ass_path if events else None, peak_s, duration,
            ass_fonts_dir(BUBBLE_FONT), ffprobe_has_audio(clip_path),
        )
        if run_ffmpeg_command(command).returncode != 0 or ffprobe_video_size(out) != (width, height):
            return False
        os.replace(out, clip_path)
        return True
    except Exception as exc:
        logger.warning("Edit kit %s not applied to %s: %s", kit["id"], clip_path.name, exc)
        return False
    finally:
        out.unlink(missing_ok=True)
        ass_path.unlink(missing_ok=True)
