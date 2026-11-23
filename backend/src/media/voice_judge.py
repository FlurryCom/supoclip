"""Cat Clips 3.0: Claude reads the transcript with what was measured around each stretch of
speech (the game's own subtitle, how loud it is, whether the webcam mouth moves) and says who
says what: the streamer or a character in the game. Owner's idea: context tells a streamer
reacting ("oh my god, hang up the phone") from a character talking ("so you're saying you ran?")
where the measurements alone can't. For a game line the subtitle shows, the subtitle's words
replace what Whisper misheard.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

MODEL = "sonnet"
TIMEOUT = 150

SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "first": {"type": "integer"},
                    "last": {"type": "integer"},
                    "speaker": {"type": "string", "enum": ["streamer", "game"]},
                    "text": {"type": "string"},
                },
                "required": ["first", "last", "speaker", "text"],
            },
        }
    },
    "required": ["lines"],
}

PROMPT = """This is the transcript of a short clip from a live stream: a streamer playing GTA with a webcam.
The audio mixes the streamer's microphone with the game's dialogue (any character in the game,
the protagonist the streamer controls included). The transcript comes from Whisper: it often
mishears game dialogue and its word timings can drift by a second or more.

Each stretch of speech below comes with what was measured while it was said:
- loud: the stretch's loudness against the clip's typical speech, in dB (the streamer's microphone
  is usually louder than the game, but game sound can make this unreliable)
- mouth: how much the streamer's mouth moves on the webcam against their usual (above 1 = moving
  more than usual, which points to the streamer talking; "no cam" when there's no webcam)
- GTA subtitle: the line the game wrote on screen at that time, read by OCR (may have OCR noise);
  game dialogue that matches it is the game's. The streamer can talk over a subtitle.

Whisper can also miss a game line entirely: never stretch a subtitle over words that don't sound like it.
Use all of it and the conversation's context: a streamer reacts, swears, talks to chat and
comments on what happens; game characters talk about the mission and each other by name.

Answer with lines covering every word number from 0 to {last} in order, without gaps or overlaps;
split wherever the speaker changes, even inside a stretch. speaker is "streamer" or "game".
text: for a game line whose words the GTA subtitle shows, the subtitle's words for exactly that
line (fix the OCR noise, drop the part of the subtitle not said in this line); otherwise "".

{stretches}"""


def stretches(words: list[dict[str, Any]], gap: float = 0.35) -> list[list[int]]:
    """Word indexes of each stretch of speech, split at pauses longer than ``gap``."""
    out: list[list[int]] = []
    for i, w in enumerate(words):
        if out and float(w["start"]) - float(words[out[-1][-1]]["end"]) <= gap:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def evidence(words: list[dict[str, Any]], groups: list[list[int]], audio: Optional[np.ndarray], rate: int,
             motion: Optional[list[float]], lines: list[tuple[float, float, list[str]]]) -> list[dict[str, Any]]:
    """What was measured for each stretch: loudness (dB vs the clip's median stretch), mouth
    (motion vs the clip's mean, or None), and the GTA subtitle on screen meanwhile."""
    spans = [(float(words[g[0]]["start"]), float(words[g[-1]]["end"])) for g in groups]
    loud = []
    if audio is not None and audio.size:
        for start, end in spans:
            piece = audio[int(start * rate):max(int(end * rate), int(start * rate) + 1)]
            loud.append(20 * np.log10(float(np.sqrt(np.mean(piece ** 2))) + 1e-6) if piece.size else None)
        known = [v for v in loud if v is not None]
        median = float(np.median(known)) if known else 0.0
        loud = [None if v is None else round(v - median, 1) for v in loud]
    else:
        loud = [None] * len(groups)
    mean_motion = float(np.mean(motion)) + 1e-6 if motion else None
    out = []
    for k, (start, end) in enumerate(spans):
        subtitle = [" ".join(text) for a, b, text in lines if a - 1.0 <= end and b + 1.0 >= start]
        out.append({
            "loud": loud[k],
            "mouth": None if motion is None else round(motion[k] / mean_motion, 2),
            "subtitle": " / ".join(subtitle),
        })
    return out


def prompt(words: list[dict[str, Any]], groups: list[list[int]], facts: list[dict[str, Any]]) -> str:
    blocks = []
    for group, fact in zip(groups, facts):
        said = " ".join(f"{i}:{str(words[i]['text']).strip()}" for i in group)
        loud = "?" if fact["loud"] is None else f"{fact['loud']:+.1f} dB"
        mouth = "no cam" if fact["mouth"] is None else f"{fact['mouth']:.2f}"
        subtitle = f'"{fact["subtitle"]}"' if fact["subtitle"] else "none"
        start, end = float(words[group[0]]["start"]), float(words[group[-1]]["end"])
        blocks.append(f"[{start:.1f}-{end:.1f}s] loud {loud} | mouth {mouth} | GTA subtitle: {subtitle}\n  {said}")
    return PROMPT.replace("{last}", str(len(words) - 1)).replace("{stretches}", "\n".join(blocks))


def _close(heard: list[str], text: list[str]) -> bool:
    """Whether ``text`` could be what Whisper misheard as ``heard`` (similar letters, similar length)."""
    from difflib import SequenceMatcher

    a, b = (" ".join(re.findall(r"[a-z0-9']+", " ".join(t).lower())) for t in (heard, text))
    return 0.7 <= len(text) / max(1, len(heard)) <= 1.5 and SequenceMatcher(None, a, b).ratio() >= 0.5


def apply_verdict(words: list[dict[str, Any]], answer: dict[str, Any], fallback: list[dict[str, Any]],
                  subtitled: set[int]) -> list[dict[str, Any]]:
    """Caption words with Claude's roles; a word Claude left out keeps its ``fallback`` role.

    A game line's ``text`` replaces its words, spread over the same time, only where a GTA
    subtitle was on screen (``subtitled`` word indexes): Claude must not rewrite anything else.
    """
    roles = [w["role"] for w in fallback]
    rewrite: dict[int, tuple[int, list[str]]] = {}
    for line in answer.get("lines") or []:
        try:
            first, last, speaker = int(line["first"]), int(line["last"]), line["speaker"]
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= first <= last < len(words) or speaker not in ("streamer", "game"):
            continue
        text = str(line.get("text") or "").split()
        if speaker == "game" and text:
            heard = [str(w["text"]) for w in words[first:last + 1]]
            if not _close(heard, text):
                continue  # Claude pinned a subtitle on words that aren't it: its call is off here
            if all(i in subtitled for i in range(first, last + 1)):
                rewrite[first] = (last, text)
        roles[first:last + 1] = [speaker] * (last - first + 1)
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(words):
        if i in rewrite and all(roles[k] == "game" for k in range(i, rewrite[i][0] + 1)):
            last, text = rewrite[i]
            start, end = float(words[i]["start"]), float(words[last]["end"])
            step = (end - start) / len(text)
            for k, token in enumerate(text):
                base = dict(words[i]) if k == 0 else {key: v for key, v in words[i].items() if key != "break_before"}
                out.append({**base, "text": token, "start": start + k * step, "end": start + (k + 1) * step,
                            "role": "game"})
            i = last + 1
            continue
        out.append({**words[i], "role": roles[i]})
        i += 1
    return out


def judge(words: list[dict[str, Any]], audio: Optional[np.ndarray], rate: int,
          motion_at: Optional[Callable[[list[float]], list[float]]],
          lines: list[tuple[float, float, list[str]]], fallback: list[dict[str, Any]],
          run: Optional[Callable[..., dict]] = None) -> list[dict[str, Any]]:
    """Claude's roles for ``words``; raises when Claude can't answer (the caller keeps ``fallback``)."""
    if run is None:
        from ..claude_cli import run_cli_images
        run = run_cli_images
    groups = stretches(words)
    middles = [(float(words[g[0]]["start"]) + float(words[g[-1]]["end"])) / 2 for g in groups]
    motion = list(motion_at(middles)) if motion_at is not None else None
    facts = evidence(words, groups, audio, rate, motion, lines)
    subtitled = {i for group, fact in zip(groups, facts) if fact["subtitle"] for i in group}
    answer = run(prompt(words, groups, facts), [], SCHEMA, model=MODEL, timeout=TIMEOUT)
    out = apply_verdict(words, answer, fallback, subtitled)
    logger.info("Caption voices by Claude: %d game, %d streamer words",
                sum(w["role"] == "game" for w in out), sum(w["role"] == "streamer" for w in out))
    return out
