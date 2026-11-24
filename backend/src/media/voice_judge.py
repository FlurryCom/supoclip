"""Cat Clips 3.0: Claude reads the transcript with what was measured around each stretch of
speech (the game's own subtitle, how loud it is, whether the webcam mouth moves) and says who
says what: the streamer or a character in the game. Owner's idea: context tells a streamer
reacting ("oh my god, hang up the phone") from a character talking ("so you're saying you ran?")
where the measurements alone can't. For a game line the subtitle shows, the subtitle's words
replace what Whisper misheard. Game lines say whose voice it is: the protagonist, another character
on screen, or someone on the phone (GTA 6's call card names the caller): the captions colour them apart.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional, Sequence

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
                    "speaker": {"type": "string", "enum": ["streamer", "protagonist", "npc", "phone", "background"]},
                    "npc": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["first", "last", "speaker", "text"],
            },
        },
        "subtitles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "speaker": {"type": "string", "enum": ["protagonist", "npc", "phone", "music", "background"]},
                    "npc": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["id", "speaker", "text"],
            },
        },
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
- cam: how surely the webcam face (the streamer) is the one talking, 0 to 1. Near 0: the voice is
  someone else's. Near 1: the streamer's mouth moves with the audio; he often talks OVER the game,
  so a high cam with words that live inside the game's scene (answering characters, addressing one
  by name) can still be a game line.
- voice: a voice-print letter; stretches with the same letter sound like the same person (it can be
  wrong on short stretches or when two people talk at once; "?" when too short to tell)
- call: while GTA's phone-call card is on screen, who is on the line with the protagonist. Then the
  game's dialogue is either the protagonist talking into the phone or the caller's voice on it: the
  voice of the character ON SCREEN holding the phone is the protagonist, never "npc".

Loudness helps tell people apart too: the streamer's microphone is usually several dB above the game's
dialogue; a character nearer the camera is louder than one further away; a caller on the phone stays at
an even level. Several characters often talk in one clip: don't put them all in one voice.

Whisper can also miss a game line entirely: never stretch a subtitle over words that don't sound like it.
Use all of it and the conversation's context: a streamer reacts, swears, talks to chat and
comments on what happens; game characters talk about the mission and each other by name.

Answer with lines covering every word number from 0 to {last} in order, without gaps or overlaps;
split wherever the speaker changes, even inside a stretch. Words that are a GTA subtitle's dialogue get
that subtitle's speaker (the subtitle's own words will replace them). speaker is one of:
- "streamer": the streamer
- "protagonist": the character the streamer plays (in GTA 6 usually Lucia, or Jason)
- "phone": the caller's voice during a call
- "npc": any other character in the game. The protagonist is who the camera follows and who answers
  for the player: in a scripted dialogue, the voice answering questions put to the player's character
  is usually the protagonist, and their lines share one voice-print letter across the clip. In GTA 6
  the playable protagonists are Lucia and Jason: a scene where one of them is questioned (police,
  therapy, interrogation) has that one answering as "protagonist".
- "background": faint ambience nobody attends — passersby, crowd chatter, distant NPCs talking
  among themselves, quiet (see loud) and unrelated to what the streamer is doing. These get NO
  caption (owner's rule). A quiet line that IS the scene, a cutscene conversation for one, keeps
  its real speaker.
Short, half-heard first-person mutters with no GTA subtitle ("what am I gonna...", "get a car") are
usually the streamer thinking aloud; the cam reading can run low when he speaks quietly or off-axis.
Follow the conversation's thread (owner's rule): game dialogue is diegetic — it sounds scripted,
advances the scene, and lines answer one another (a question gets a reply, an order gets a protest).
When a stretch reads like the reply to the previous game line, it is the game's even with no other
proof: two voices trading in-world lines are characters talking to EACH OTHER, not the streamer.
The streamer speaks from outside the scene: he reacts, swears at the game, talks to chat, and calls
characters "he", "she", "this guy" — he doesn't deliver generic in-world lines mid-scene.
npc: for an "npc" line, which character: 1, 2, 3... the same number for the same character all clip long.
text: for a game line whose words the GTA subtitle shows, the subtitle's words for exactly that
line (fix the OCR noise, drop the part of the subtitle not said in this line); otherwise "".

Then, under subtitles, one entry per GTA subtitle listed at the end (id = its S number), even when the
transcript missed it (Whisper drops game lines the streamer talks over): who says it (protagonist, npc
with its number, phone, "music" when it is a song's lyrics (GTA subtitles those too, in a club or
on the radio), or "background" for faint chatter unrelated to the scene, which also covers a mission OBJECTIVE the game wrote on
screen (an imperative instruction like "Get in X's car", not something a voice says) — music and background
lines are not captioned — and its words with the OCR noise fixed.

{stretches}{subtitles}"""


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
             motion: Optional[list[float]], lines: list[tuple[float, float, list[str]]],
             calls: Sequence[tuple[float, float, str]] = ()) -> list[dict[str, Any]]:
    """What was measured for each stretch: loudness (dB vs the clip's median stretch), mouth
    (motion vs the clip's mean, or None), the GTA subtitle on screen meanwhile, and the caller."""
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
            "call": _caller_at(calls, start, end) or "",
        })
    return out


def _caller_at(calls: Sequence[tuple[float, float, str]], start: float, end: float) -> Optional[str]:
    return next((name for a, b, name in calls if a <= end and b >= start), None)


def voice_letters(audio: Optional[np.ndarray], rate: int, spans: list[tuple[float, float]]) -> list[str]:
    """A voice-print letter per span (the same letter: the same voice), "?" when too short or unknown."""
    if audio is None or not len(spans):
        return ["?"] * len(spans)
    from .voice_roles import cluster, embed

    vectors, owners = [], []
    for k, (a, b) in enumerate(spans):
        vector = embed(audio[int(a * rate):int(b * rate)])
        if vector is not None:
            vectors.append(vector / (np.linalg.norm(vector) + 1e-9))
            owners.append(k)
    letters = ["?"] * len(spans)
    for k, label in zip(owners, cluster(vectors)):
        letters[k] = chr(ord("A") + label % 26)
    return letters


def _loud(audio: Optional[np.ndarray], rate: int, a: float, b: float, median: float) -> Optional[float]:
    if audio is None:
        return None
    piece = audio[int(a * rate):max(int(b * rate), int(a * rate) + 1)]
    return round(20 * np.log10(float(np.sqrt(np.mean(piece ** 2))) + 1e-6) - median, 1) if piece.size else None


def subtitle_block(lines: list[tuple[float, float, list[str]]], letters: list[str], louds: list[Optional[float]],
                   calls: Sequence[tuple[float, float, str]] = ()) -> str:
    if not lines:
        return ""
    rows = []
    for k, (a, b, text) in enumerate(lines):
        loud = "?" if louds[k] is None else f"{louds[k]:+.1f} dB"
        call = _caller_at(calls, a, b)
        rows.append(f"S{k} [{a:.1f}-{b:.1f}s] loud {loud} | voice {letters[k]}" + (f" | call: {call}" if call else "")
                    + f'\n  "{" ".join(text)}"')
    return "\n\nGTA subtitles:\n" + "\n".join(rows)


def prompt(words: list[dict[str, Any]], groups: list[list[int]], facts: list[dict[str, Any]],
           subtitles: str = "", title: str = "") -> str:
    blocks = []
    if title:
        blocks.append(f"The clip's editor titled this moment: {title!r} — it says what happens in the scene.")
    for group, fact in zip(groups, facts):
        said = " ".join(f"{i}:{str(words[i]['text']).strip()}" for i in group)
        loud = "?" if fact["loud"] is None else f"{fact['loud']:+.1f} dB"
        mouth = "no cam" if fact["mouth"] is None else f"{fact['mouth']:.2f}"
        subtitle = f'"{fact["subtitle"]}"' if fact["subtitle"] else "none"
        start, end = float(words[group[0]]["start"]), float(words[group[-1]]["end"])
        call = f" | call: {fact['call']}" if fact.get("call") else ""
        voice = f" | voice {fact['voice']}" if fact.get("voice") else ""
        if fact.get("cam") is not None:
            voice = f" | cam {fact['cam']:.2f}{voice}"
        blocks.append(f"[{start:.1f}-{end:.1f}s] loud {loud} | mouth {mouth}{voice} | GTA subtitle: {subtitle}{call}\n  {said}")
    return (PROMPT.replace("{last}", str(len(words) - 1)).replace("{stretches}", "\n".join(blocks))
            .replace("{subtitles}", subtitles))


def _close(heard: list[str], text: list[str]) -> bool:
    """Whether ``text`` could be what Whisper misheard as ``heard`` (similar letters, similar length)."""
    from difflib import SequenceMatcher

    a, b = (" ".join(re.findall(r"[a-z0-9']+", " ".join(t).lower())) for t in (heard, text))
    return 0.7 <= len(text) / max(1, len(heard)) <= 1.5 and SequenceMatcher(None, a, b).ratio() >= 0.5


GAME_VOICES = ("protagonist", "npc", "phone", "background")


def apply_verdict(words: list[dict[str, Any]], answer: dict[str, Any], fallback: list[dict[str, Any]],
                  subtitled: set[int], calls: Sequence[tuple[float, float, str]] = (),
                  low: frozenset[int] | set[int] = frozenset(),
                  high: frozenset[int] | set[int] = frozenset()) -> list[dict[str, Any]]:
    """Caption words with Claude's roles; a word Claude left out keeps its ``fallback`` role.

    A game word also gets its ``voice`` (protagonist, npc or phone; a phone line its ``caller``).
    ``low``: word indexes whose cam sync says the streamer is NOT talking; they can't be his.
    A game line's ``text`` replaces its words, spread over the same time, only where a GTA
    subtitle was on screen (``subtitled`` word indexes): Claude must not rewrite anything else.
    """
    roles = [w["role"] for w in fallback]
    voices: list[Optional[str]] = [w.get("voice") for w in fallback]
    npcs: list[Optional[int]] = [w.get("npc") for w in fallback]
    rewrite: dict[int, tuple[int, list[str]]] = {}
    for line in answer.get("lines") or []:
        try:
            first, last, speaker = int(line["first"]), int(line["last"]), line["speaker"]
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= first <= last < len(words) or speaker not in ("streamer", "game", *GAME_VOICES):
            continue
        voice = speaker if speaker in GAME_VOICES else None
        if voice == "npc" and isinstance(line.get("npc"), int):
            npcs[first:last + 1] = [line["npc"]] * (last - first + 1)
        if speaker == "phone" and _caller_at(calls, float(words[first]["start"]), float(words[last]["end"])) is None:
            voice = "npc"  # no call card on screen: a voice off the phone is just another character
        speaker = "streamer" if speaker == "streamer" else "game"
        text = str(line.get("text") or "").split()
        if speaker == "game" and text:
            heard = [str(w["text"]) for w in words[first:last + 1]]
            if not _close(heard, text):
                continue  # Claude pinned a subtitle on words that aren't it: its call is off here
            if all(i in subtitled for i in range(first, last + 1)):
                rewrite[first] = (last, text)
        roles[first:last + 1] = [speaker] * (last - first + 1)
        voices[first:last + 1] = [voice] * (last - first + 1)
    for i in low:
        if roles[i] == "streamer":
            roles[i] = "game"
    for i in high:
        if roles[i] == "game" and i not in subtitled:  # the game's own subtitle is harder proof
            roles[i] = "streamer"
            voices[i] = None

    def tagged(word: dict[str, Any], k: int) -> dict[str, Any]:
        out = {**word, "role": roles[k]}
        out.pop("voice", None)
        out.pop("caller", None)
        out.pop("npc", None)
        voice = voices[k]
        on_call = _caller_at(calls, float(word["start"]), float(word["end"]))
        if roles[k] == "game" and on_call and voice != "protagonist":
            voice = "phone"  # owner's rule: during a call the other voice is the caller's
        if roles[k] == "game" and voice:
            out["voice"] = voice
            if voice == "phone":
                out["caller"] = on_call
            if voice == "npc" and npcs[k] is not None:
                out["npc"] = npcs[k]
        return out

    out: list[dict[str, Any]] = []
    i = 0
    while i < len(words):
        if i in rewrite and all(roles[k] == "game" for k in range(i, rewrite[i][0] + 1)):
            last, text = rewrite[i]
            start, end = float(words[i]["start"]), float(words[last]["end"])
            step = (end - start) / len(text)
            for k, token in enumerate(text):
                base = dict(words[i]) if k == 0 else {key: v for key, v in words[i].items() if key != "break_before"}
                out.append({**tagged(base, i), "text": token, "start": start + k * step,
                            "end": start + (k + 1) * step})
            i = last + 1
            continue
        out.append(tagged(words[i], i))
        i += 1
    return out


LOW_CAM = 0.30  # at or under this, the webcam face is not the one talking
HIGH_CAM = 0.70  # at or over this, the words are the streamer's: on the owner's gabarito no real game
# line ever scored this high (windowed scoring), while the streamer's taunts that READ like NPC barks do


def _roles_of(words: list[dict[str, Any]], answer: dict[str, Any]):
    roles: list[Optional[str]] = [None] * len(words)
    voices: list[Optional[str]] = [None] * len(words)
    for line in answer.get("lines") or []:
        try:
            first, last, speaker = int(line["first"]), int(line["last"]), line["speaker"]
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= first <= last < len(words) or speaker not in ("streamer", "game", *GAME_VOICES):
            continue
        role = "streamer" if speaker == "streamer" else "game"
        voice = speaker if speaker in GAME_VOICES else None
        roles[first:last + 1] = [role] * (last - first + 1)
        voices[first:last + 1] = [voice] * (last - first + 1)
    return roles, voices


def _sub_speakers(answer: dict[str, Any]) -> dict:
    return {v.get("id"): v for v in answer.get("subtitles") or [] if isinstance(v, dict)}


def _disputed_subtitles(first: dict[str, Any], second: dict[str, Any]) -> list:
    one, two = _sub_speakers(first), _sub_speakers(second)
    return [(one.get(k) or two.get(k), two.get(k)) for k in set(one) | set(two)
            if (one.get(k) or {}).get("speaker") != (two.get(k) or {}).get("speaker")]


def _majority_subtitles(disputed: list, kept: list, second: dict[str, Any], third: dict[str, Any]) -> list:
    """The agreed subtitle verdicts plus, for each disputed one, whichever speaker the third pass sides with."""
    got = {v.get("id") for v in kept}
    sec, thr = _sub_speakers(second), _sub_speakers(third)
    for first_v, _second_v in disputed:
        k = first_v.get("id")
        if k in got:
            continue
        tie = (thr.get(k) or {}).get("speaker")
        for candidate in (first_v, sec.get(k)):
            if candidate and candidate.get("speaker") == tie:
                kept.append(candidate)
                got.add(k)
                break
    return kept


def merge_answers(words: list[dict[str, Any]], first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """What the two passes agree on: Claude is nondeterministic, and only its stable calls count.

    Lines of the first answer survive where the second gives every word the same role (and the same
    game voice, when both name one); a subtitle verdict survives when both passes name the same
    speaker. Words left uncovered keep the fallback role in ``apply_verdict``.
    """
    r1, v1 = _roles_of(words, first)
    r2, v2 = _roles_of(words, second)
    disputed = {i for i in range(len(words))
                if r1[i] != r2[i] or (r1[i] == "game" and None not in (v1[i], v2[i]) and v1[i] != v2[i])}
    lines = [line for line in first.get("lines") or []
             if isinstance(line, dict) and isinstance(line.get("first"), int) and isinstance(line.get("last"), int)
             and not any(i in disputed for i in range(max(0, line["first"]), min(len(words), line["last"] + 1)))]
    second_speaker = {v.get("id"): v.get("speaker") for v in second.get("subtitles") or [] if isinstance(v, dict)}
    subtitles = [v for v in first.get("subtitles") or []
                 if isinstance(v, dict) and second_speaker.get(v.get("id")) == v.get("speaker")]
    kept = {v.get("id") for v in subtitles}
    for source in (first, second):  # lyrics are dropped captions: one pass calling music is enough
        subtitles += [v for v in source.get("subtitles") or []
                      if isinstance(v, dict) and v.get("speaker") == "music" and v.get("id") not in kept
                      and not kept.add(v.get("id"))]
    return {"lines": lines, "subtitles": subtitles}


def judge(words: list[dict[str, Any]], audio: Optional[np.ndarray], rate: int,
          motion_at: Optional[Callable[[list[float]], list[float]]],
          lines: list[tuple[float, float, list[str]]], fallback: list[dict[str, Any]],
          run: Optional[Callable[..., dict]] = None,
          calls: Sequence[tuple[float, float, str]] = (),
          speak_at: Optional[Callable[[list[tuple[float, float]]], Optional[list[float]]]] = None,
          title: str = "") -> list[dict[str, Any]]:
    """Claude's roles for ``words``, twice-agreed; raises when Claude can't answer (the caller keeps
    ``fallback``)."""
    if run is None:
        from ..claude_cli import run_cli_images
        run = run_cli_images
    groups = stretches(words)
    middles = [(float(words[g[0]]["start"]) + float(words[g[-1]]["end"])) / 2 for g in groups]
    motion = list(motion_at(middles)) if motion_at is not None else None
    facts = evidence(words, groups, audio, rate, motion, lines, calls)
    spans = [(float(words[g[0]]["start"]), float(words[g[-1]]["end"])) for g in groups]
    # Measured on the owner's gabarito: a HIGH cam is proof (his lips move with these words), while a
    # low one is only a hint (he scores low whenever he speaks quietly or off-axis).
    speaking = speak_at(spans) if speak_at is not None else None
    high: set[int] = set()
    if speaking is not None:
        for k, score in enumerate(speaking[:len(facts)]):
            if score is not None:
                facts[k]["cam"] = score
        high = {i for group, score in zip(groups, speaking) if score is not None and score >= HIGH_CAM
                for i in group}
    letters = voice_letters(audio, rate, spans + [(a, b + 1 / 3) for a, b, _ in lines])
    for fact, letter in zip(facts, letters):
        fact["voice"] = letter
    median = _median_db(audio, rate, spans)
    louds = [_loud(audio, rate, a, b + 1 / 3, median) for a, b, _ in lines]
    subtitled = {i for group, fact in zip(groups, facts) if fact["subtitle"] for i in group}
    ask = prompt(words, groups, facts, subtitle_block(lines, letters[len(spans):], louds, calls), title)
    answer = run(ask, [], SCHEMA, model=MODEL, timeout=TIMEOUT)
    try:
        second = run(ask, [], SCHEMA, model=MODEL, timeout=TIMEOUT)
        disputed_subs = _disputed_subtitles(answer, second)
        answer = merge_answers(words, answer, second)
        if disputed_subs:  # who says a subtitle flaps between passes: a third one breaks the tie
            third = run(ask, [], SCHEMA, model=MODEL, timeout=TIMEOUT)
            answer["subtitles"] = _majority_subtitles(disputed_subs, answer.get("subtitles") or [],
                                                      second, third)
    except Exception as exc:
        logger.warning("Second judge pass failed (%s); using the first alone", exc)
    kept = fading(lines, louds)
    faded = [k for k, line in enumerate(lines) if line not in kept]
    if faded:  # these lines and the words heard of them are not captioned, whatever Claude said
        verdicts = [v for v in answer.get("subtitles") or [] if v.get("id") not in faded]
        answer["subtitles"] = verdicts + [{"id": k, "speaker": "background", "text": ""} for k in faded]
        logger.info("GTA lines faded out (quieter than the scene): %s", [" ".join(lines[k][2]) for k in faded])
    out = with_subtitles(apply_verdict(words, answer, fallback, subtitled, calls, high=high), lines,
                         answer.get("subtitles") or [], calls, timer=make_timer(audio, rate))
    logger.info("Caption voices by Claude: %d streamer, %d protagonist, %d npc, %d phone words (calls: %s)",
                sum(w["role"] == "streamer" for w in out),
                *(sum(w.get("voice") == v for w in out) for v in ("protagonist", "npc", "phone")),
                calls or "none")
    return out


def make_timer(audio: Optional[np.ndarray], rate: int) -> Optional[Callable[[str, float, float], list[tuple[str, float, float]]]]:
    """A line-timer over this clip's audio (None without audio): when the words of an unheard line start."""
    if audio is None or not getattr(audio, "size", 0):
        return None
    from .align import line_times

    return lambda text, a, b: line_times(audio, rate, text, a, b)


FADE_DB = 2.5  # a subtitled line this far under the clip's typical game line has faded out of the scene
FADE_MIN_LINES = 5  # too few lines for a typical level: never filter


def fading(lines: list[tuple[float, float, list[str]]], louds: list[Optional[float]],
           drop: float = FADE_DB) -> list[tuple[float, float, list[str]]]:
    """Owner's rule: a GTA line much quieter than the clip's other game lines is background — the
    player walked away from the talker, or it is the club's song — and gets no caption."""
    known = [v for v in louds if v is not None]
    if len(known) < FADE_MIN_LINES:
        return lines
    floor = float(np.median(known)) - drop
    return [line for line, loud in zip(lines, louds) if loud is None or loud >= floor]


def _median_db(audio: Optional[np.ndarray], rate: int, spans: list[tuple[float, float]]) -> float:
    louds = [v for v in (_loud(audio, rate, a, b, 0.0) for a, b in spans) if v is not None]
    return float(np.median(louds)) if louds else 0.0


def _matched_span(line: list[str], said: list[str]) -> Optional[tuple[int, int]]:
    """Where in ``said`` the subtitle ``line`` was heard (first, last index), when at least two of its words
    match in order (or one long word of a short line); the span keeps the line's length around them."""
    from difflib import SequenceMatcher

    pairs = [(block.a + k, block.b + k) for block in SequenceMatcher(None, line, said, autojunk=False)
             .get_matching_blocks() for k in range(block.size)]
    if len(pairs) < 2 and not (pairs and len(line[pairs[0][0]]) >= 4 and len(line) <= 3):
        return None
    return max(0, pairs[0][1] - pairs[0][0]), min(len(said) - 1, pairs[-1][1] + len(line) - 1 - pairs[-1][0])


def with_subtitles(words: list[dict[str, Any]], lines: list[tuple[float, float, list[str]]],
                   verdicts: list[dict[str, Any]], calls: Sequence[tuple[float, float, str]] = (),
                   timer: Optional[Callable[[str, float, float], list[tuple[str, float, float]]]] = None) -> list[dict[str, Any]]:
    """Every GTA subtitle becomes a game caption line (owner's rule: the subtitle is proof of who talks
    and what is said), even when Whisper missed it or heard it mixed into the streamer's talk.

    The words that were the subtitle's (game words near it, or words that match it) give way to the
    subtitle's own words over their time; a subtitle nobody heard gets its words timed by ``timer`` (the audio's own
    alignment: GTA draws a line before its voice starts), or over the drawn time without one. The streamer's words around stay; when they overlap a line, both show.
    """
    from .voice_roles import _token_list

    if not lines:
        return words
    verdict = {int(v["id"]): v for v in verdicts if isinstance(v.get("id"), int)}
    lyric = {k for k, v in verdict.items() if v.get("speaker") in ("music", "background")}
    tokens = [(_token_list(str(w.get("text", ""))) or [""])[0] for w in words]
    owner: dict[int, int] = {}  # word -> the subtitle line it was

    def gap(i: int, k: int) -> float:
        mid = (float(words[i]["start"]) + float(words[i]["end"])) / 2
        return max(lines[k][0] - mid, mid - lines[k][1], 0.0)

    matched: dict[int, set[int]] = {}
    windows = []
    for k, (a, b, line) in enumerate(lines):
        window = [i for i, w in enumerate(words) if a - 1.0 <= float(w["start"]) <= b + 1.0]
        windows.append(window)
        span = _matched_span(line, [tokens[i] for i in window]) if window else None
        for i in (window[span[0]:span[1] + 1] if span else []):
            matched.setdefault(i, set()).add(k)
    for i, ks in matched.items():  # words that match a line are that line's (the nearest, if several)
        owner[i] = min(ks, key=lambda k: gap(i, k))
    line_tokens = [set(line) for _a, _b, line in lines]
    for k, window in enumerate(windows):  # a game word sharing a word with the line joins it too
        for i in window:
            if words[i]["role"] == "game" and i not in matched and tokens[i] in line_tokens[k] \
                    and (i not in owner or gap(i, k) < gap(i, owner[i])):
                owner[i] = k
    blocks: list[tuple[float, int, list[dict[str, Any]]]] = []
    for k, (a, b, line) in enumerate(lines):
        if k in lyric:
            continue  # a song's lyrics: neither the line nor the words Whisper heard of it are captions
        mine = sorted(i for i, o in owner.items() if o == k)
        start, end = (float(words[mine[0]]["start"]), float(words[mine[-1]]["end"])) if mine else (a, b + 1 / 3)
        v = verdict.get(k, {})
        text = str(v.get("text") or "").split()
        if not text or not 0.6 <= len(text) / max(1, len(line)) <= 1.6:
            text = line  # Claude's clean-up must stay the subtitle's words
        timed = timer(" ".join(text), a, b) if timer is not None and not mine else None
        voice = v.get("speaker") if v.get("speaker") in GAME_VOICES else next(
            (words[i].get("voice") for i in mine if words[i].get("voice")), "npc")
        caller = _caller_at(calls, start, end)
        if caller and voice != "protagonist":
            voice = "phone"
        elif voice == "phone":
            voice = "npc"
        base: dict[str, Any] = {"role": "game", "voice": voice}
        if mine and words[mine[0]].get("break_before"):
            base["break_before"] = True
        if voice == "phone":
            base["caller"] = caller
        if voice == "npc" and isinstance(v.get("npc"), int):
            base["npc"] = v["npc"]
        if timed and len(timed) == len(text):
            spans = [(ts, te) for _, ts, te in timed]
            start = spans[0][0]
        else:
            step = (end - start) / len(text)
            spans = [(start + j * step, start + (j + 1) * step) for j in range(len(text))]
        blocks.append((start, k, [{**(base if j == 0 else {kk: vv for kk, vv in base.items() if kk != "break_before"}),
                                   "text": token, "start": spans[j][0], "end": spans[j][1]}
                                  for j, token in enumerate(text)]))
    blocks.sort(key=lambda item: item[0])
    for (s1, _k1, ws1), (s2, _k2, _ws2) in zip(blocks, blocks[1:]):
        # GTA shows one subtitle at a time: a line leaves when the next arrives
        limit = s2 - 0.05
        last = max(float(w["end"]) for w in ws1)
        if last <= limit or limit <= s1 + 0.2:
            continue
        scale = (limit - s1) / (last - s1)
        for w in ws1:
            w["start"] = round(s1 + (float(w["start"]) - s1) * scale, 3)
            w["end"] = round(s1 + (float(w["end"]) - s1) * scale, 3)
    kept = [(float(w["start"]), i, w) for i, w in enumerate(words)
            if i not in owner or owner[i] in lyric]
    kept = [(t, i, w) for t, i, w in kept if i not in owner or w["role"] != "game"]
    out: list[dict[str, Any]] = []
    # a line's words stay together; the streamer's words said meanwhile follow it
    items = sorted([(start, 0, k, ws) for start, k, ws in blocks] + [(t, 1, i, [w]) for t, i, w in kept],
                   key=lambda item: (item[0], item[1], item[2]))
    for _, _, _, ws in items:
        out.extend(ws)
    logger.info("GTA subtitles: %d lines, %d not heard by Whisper", len(lines),
                sum(1 for k in range(len(lines)) if k not in owner.values()))
    return [w for w in out if w.get("voice") != "background"]
