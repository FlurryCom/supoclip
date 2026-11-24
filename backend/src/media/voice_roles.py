"""Who is talking in a live clip (fork, Cat Clips v2): the streamer, a friend, or the game.

Speech is cut into stretches between pauses; a small speaker-embedding model (CAM++ from
3D-Speaker, ONNX, CPU) turns each stretch into a voice vector, and stretches with the same
voice are grouped. The streamer's group is the one whose speech lines up with the mouth
moving in the webcam (or, without a cam, the one that talks most). Other voices are the game
when the game shows its own subtitles while they talk, a friend when they talk back and forth
with the streamer, and unsure otherwise (owner: unsure lines are left out).
Any failure keeps every word as the streamer, exactly like captions were before.
"""
from __future__ import annotations

import logging
import os
import urllib.request
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

RATE = 16000
MODEL_PATH = Path(os.getenv("VOICE_MODEL_PATH", "/app/uploads/models/voice/campplus.onnx"))
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
             "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx")
MIN_EMBED_SECONDS = 0.8
_session = None


def speech_segments(words: list[dict[str, Any]], gap: float = 0.6) -> list[tuple[float, float, list[int]]]:
    """Stretches of speech (start s, end s, word indexes) split at pauses longer than ``gap``.

    Words are caption words: ``start``/``end`` in seconds on the clip's own timeline.
    """
    segments: list[tuple[float, float, list[int]]] = []
    for index, word in enumerate(words):
        start, end = float(word["start"]), float(word["end"])
        if segments and start - segments[-1][1] <= gap:
            s, _, idx = segments[-1]
            segments[-1] = (s, max(end, segments[-1][1]), idx + [index])
        else:
            segments.append((start, end, [index]))
    return segments


def _mel_banks(bins: int = 80, fft: int = 512, low: float = 20.0, high: float = RATE / 2) -> np.ndarray:
    def mel(f):
        return 1127.0 * np.log(1.0 + np.asarray(f) / 700.0)

    points = np.linspace(mel(low), mel(high), bins + 2)
    freqs = mel(np.arange(fft // 2 + 1) * RATE / fft)
    banks = np.zeros((bins, fft // 2 + 1), dtype=np.float32)
    for b in range(bins):
        left, centre, right = points[b], points[b + 1], points[b + 2]
        up = (freqs - left) / (centre - left)
        down = (right - freqs) / (right - centre)
        banks[b] = np.clip(np.minimum(up, down), 0, None)
    return banks


_BANKS = _mel_banks()


def fbank(samples: np.ndarray, rate: int = RATE) -> np.ndarray:
    """Kaldi-style 80-bin log-mel features (25 ms window, 10 ms hop), mean removed per clip."""
    frame, hop, fft = int(0.025 * rate), int(0.010 * rate), 512
    if len(samples) < frame:
        return np.zeros((0, 80), dtype=np.float32)
    count = 1 + (len(samples) - frame) // hop
    index = np.arange(frame)[None, :] + hop * np.arange(count)[:, None]
    frames = samples[index].astype(np.float32) * 32768.0
    frames -= frames.mean(axis=1, keepdims=True)
    frames[:, 1:] -= 0.97 * frames[:, :-1]
    frames[:, 0] *= 1 - 0.97
    frames *= (np.hanning(frame) ** 0.85).astype(np.float32)  # Povey window
    power = np.abs(np.fft.rfft(frames, n=fft)) ** 2
    feats = np.log(np.maximum(power @ _BANKS.T, 1e-10)).astype(np.float32)
    return feats - feats.mean(axis=0, keepdims=True)


MODEL_SHA256 = "98daf9bcf1c8abf3db374fec4c27a5eda7c58705bb3f180d057b085a72a88bb5"


def ensure_model() -> Optional[Path]:
    """The voice model on disk, downloaded and checked once; None when it can't be had."""
    if MODEL_PATH.exists():
        return MODEL_PATH
    import hashlib
    import tempfile

    try:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        # A file per process: two workers downloading at once never write into each other's file.
        with tempfile.NamedTemporaryFile(dir=MODEL_PATH.parent, suffix=".part", delete=False) as part:
            digest = hashlib.sha256()
            with urllib.request.urlopen(MODEL_URL, timeout=30) as response:
                while chunk := response.read(1 << 20):
                    part.write(chunk)
                    digest.update(chunk)
        if digest.hexdigest() != MODEL_SHA256:
            raise ValueError("voice model checksum mismatch")
        os.replace(part.name, MODEL_PATH)
        return MODEL_PATH
    except Exception as exc:
        logger.warning("Voice model unavailable (%s); captions keep one style", exc)
        if "part" in locals():
            Path(part.name).unlink(missing_ok=True)
        return None


def _model():
    global _session
    if _session is None:
        import onnxruntime as ort

        path = ensure_model()
        if path is None:
            raise RuntimeError("voice model missing")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2  # share the CPU with ffmpeg and the other workers
        try:
            _session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        except Exception:
            path.unlink(missing_ok=True)  # a broken file would fail every clip; fetch it again next time
            raise
    return _session


def embed(samples: np.ndarray) -> Optional[np.ndarray]:
    """Unit-length voice vector for a stretch of 16 kHz speech; None when it is too short."""
    if len(samples) < MIN_EMBED_SECONDS * RATE:
        return None
    feats = fbank(samples)[None, :, :]
    vector = _model().run(None, {"x": feats})[0][0]
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else None


def cluster(vectors: list[np.ndarray], threshold: float = 0.35) -> list[int]:
    """Group voice vectors (average-link, cosine); labels numbered by first appearance."""
    groups = [[i] for i in range(len(vectors))]
    if not groups:
        return []
    sim = np.array([[float(np.dot(a, b)) for b in vectors] for a in vectors])
    while len(groups) > 1:
        best, pair = -2.0, None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                score = float(sim[np.ix_(groups[i], groups[j])].mean())
                if score > best:
                    best, pair = score, (i, j)
        if best < threshold or pair is None:
            break
        i, j = pair
        groups[i] += groups.pop(j)
    owner = {member: g for g, members in enumerate(groups) for member in members}
    order: dict[int, int] = {}
    return [order.setdefault(owner[i], len(order)) for i in range(len(vectors))]


MOUTH_LIFT_MIN = 0.15  # the streamer's mouth must move clearly more while their voice talks


def assign_roles(
    sequence: list[int],
    seconds: dict[int, float],
    mouth_lift: Optional[dict[int, float]],
    subs_share: dict[int, float],
) -> dict[int, str]:
    """Voice group -> "streamer" | "game" | "friend" | "unsure".

    ``sequence``: each speech stretch's group, in time order. ``mouth_lift``: how much more the
    webcam mouth moves while each group talks (None without a cam). ``subs_share``: share of each
    group's speech with the game's own subtitles on screen.
    """
    if not seconds:
        return {}
    if mouth_lift and max(mouth_lift.values()) >= MOUTH_LIFT_MIN:
        # Every group the mouth clearly moves for: shouting can split the streamer's voice in two.
        streamers = {g for g, lift in mouth_lift.items() if lift >= MOUTH_LIFT_MIN}
    else:
        streamers = {max(seconds, key=seconds.get)}
    # Back-and-forth with the streamer: how often a group's stretch sits next to the streamer's.
    turns = {group: 0 for group in seconds}
    for a, b in zip(sequence, sequence[1:]):
        if (a in streamers) != (b in streamers):
            turns[a if b in streamers else b] += 1
    roles = {}
    for group in seconds:
        if group in streamers:
            roles[group] = "streamer"
        elif subs_share.get(group, 0.0) >= 0.5:
            roles[group] = "game"
        elif turns[group] >= 2 and subs_share.get(group, 0.0) < 0.2:
            roles[group] = "friend"
        else:
            roles[group] = "unsure"  # owner: when in doubt, leave it out
    return roles


def _frames(path: Path, times: list[float]):
    """Yield (frame index, frame) for the frames at ``times``, reading the video forward once.

    Seeking re-decodes from the previous keyframe each time; one pass decodes each frame once.
    """
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        wanted = {max(0, int(round(t * fps))) for t in times}
        last = max(wanted, default=-1)
        index = 0
        while index <= last and cap.grab():
            if index in wanted:
                ok, frame = cap.retrieve()
                if ok:
                    yield index, frame
            index += 1
    finally:
        cap.release()


def _index(path: Path, times: list[float]) -> list[int]:
    import cv2

    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()
    return [max(0, int(round(t * fps))) for t in times]


def mouth_motion(path: Path, face: tuple[float, float, float, float], times: list[float]) -> np.ndarray:
    """How much the lower half of the webcam face changes around each time (mean grey difference)."""
    import cv2

    fx, fy, fw, fh = (int(v) for v in face)
    y0, y1, x0, x1 = fy + fh // 2, fy + fh, fx, fx + fw
    firsts, seconds_ = _index(path, times), _index(path, [t + 0.1 for t in times])
    seconds_ = [b if b > a else a + 1 for a, b in zip(firsts, seconds_)]
    crops: dict[int, np.ndarray] = {}
    for index, frame in _frames(path, [*times, *(t + 0.1 for t in times)]):
        crops[index] = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)
    # Frames the time list can't name exactly (the +1 fallback) are read in a second short pass.
    missing = [i for i in seconds_ if i not in crops]
    if missing:
        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.release()
        for index, frame in _frames(path, [i / fps for i in missing]):
            crops[index] = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)
    values = []
    for a, b in zip(firsts, seconds_):
        ga, gb = crops.get(a), crops.get(b)
        values.append(float(np.abs(ga - gb).mean()) if ga is not None and gb is not None and ga.size else 0.0)
    return np.array(values, dtype=np.float32)


def plain_subtitle_on(frame: np.ndarray) -> bool:
    """A centred line of plain light text near the bottom (GTA-style subtitles, no outline)."""
    import cv2

    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    region = gray[int(0.80 * h):int(0.97 * h), int(0.2 * w):int(0.8 * w)]
    bright = region > 200
    if bright.mean() > 0.08:  # a bright sky or a white wall, not text
        return False
    rows = np.flatnonzero(bright.sum(axis=1) > 0.004 * region.shape[1])
    if len(rows) < 0.012 * h:
        return False
    tallest = max(len(run) for run in np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1))
    if tallest > 0.025 * h:  # GTA's line is ~1.5% of the height; an editor's burned-in caption is ~5%
        return False
    cols = np.flatnonzero(bright[rows].any(axis=0))
    left, right = int(cols[0]), int(cols[-1])
    centre = (left + right) / 2 - region.shape[1] / 2
    return right - left >= 0.06 * w and abs(centre) <= 0.12 * w


def objective_text_on(frame: np.ndarray) -> bool:
    """GTA 6 writes mission objectives where subtitles go, with the key word coloured (pink/yellow);
    spoken dialogue is plain white. A coloured word on the white text's own rows means an objective.
    Only the text rows count: a club's neon elsewhere in the band must not trip this."""
    import cv2

    h, w = frame.shape[:2]
    band = frame[int(0.80 * h):int(0.97 * h), int(0.2 * w):int(0.8 * w)]
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY) if band.ndim == 3 else band
    white = gray > 200
    rows = np.flatnonzero(white.sum(axis=1) > 0.004 * band.shape[1])
    if not rows.size:
        return False
    sel = band[rows].astype(int)
    high = sel.max(axis=2)
    colored = (high > 150) & ((high - sel.min(axis=2)) > 60)
    colored[:, :60] = False  # scene lights bleed in at the band's edges; the key word sits inside
    colored[:, -60:] = False
    return int(colored.sum()) > 0.15 * max(int(white[rows].sum()), 1)


def objective_times(path: Path, times: list[float]) -> list[bool]:
    """Whether each sampled frame shows objective/overlay text (a coloured key word)."""
    seen = {index: objective_text_on(frame) for index, frame in _frames(path, times)}
    return [seen.get(index, False) for index in _index(path, times)]


SUBTITLE_HZ = 3  # GTA keeps a subtitle up for a second or more


def _token_list(text: str) -> list[str]:
    """Lower-case words of a line, in order; OCR reads a capital I as a bar."""
    import re

    return re.findall(r"[a-z0-9']+", text.replace("|", "I").replace("\u2019", "'").lower())


def _tokens(text: str) -> set[str]:
    return set(_token_list(text))


def subtitle_strokes(frame: np.ndarray) -> np.ndarray:
    """Thin bright strokes in the subtitle band: a white top-hat keeps letter strokes and drops broad
    bright areas (sunlit ground, sky), which defeated a plain brightness test on daylight scenes."""
    import cv2

    h, w = frame.shape[:2]
    band = frame[int(0.78 * h):int(0.97 * h), int(0.2 * w):int(0.8 * w)]
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY) if band.ndim == 3 else band
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, h // 72), max(9, h // 72)))
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    return (tophat > 70) & (gray > 170)


def subtitle_on(frame: np.ndarray) -> bool:
    """A centred block of text strokes in the band (one or two lines of GTA's subtitle)."""
    strokes = subtitle_strokes(frame)
    rows = np.flatnonzero(strokes.sum(axis=1) > 0.01 * strokes.shape[1])
    if len(rows) < max(8, frame.shape[0] // 135):
        return False
    cols = np.flatnonzero(strokes[rows].any(axis=0))
    centre = (cols[0] + cols[-1]) / 2 - strokes.shape[1] / 2
    return cols[-1] - cols[0] > 0.08 * strokes.shape[1] and abs(centre) < 0.15 * strokes.shape[1]


def subtitle_text(frame: np.ndarray) -> str:
    """What the game's subtitle says ('' when there is none or it is an objective), read with tesseract."""
    import subprocess

    import cv2

    if not subtitle_on(frame) or objective_text_on(frame):
        return ""
    ink = np.where(subtitle_strokes(frame), 0, 255).astype(np.uint8)  # dark text on white reads best
    ink = cv2.resize(ink, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    png = cv2.imencode(".png", ink)[1].tobytes()
    text = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "6", "-l", "eng"], input=png,
                          capture_output=True, timeout=20, check=True).stdout.decode(errors="ignore").strip()
    if "***" in text:
        return ""  # a censored chat/TTS overlay (the streamer's own), never GTA dialogue
    return text


def subtitle_tokens(path: Path, times: list[float]) -> list[list[str]]:
    """The words of the game's subtitle on screen at each time, in order."""
    seen = {index: _token_list(subtitle_text(frame)) for index, frame in _frames(path, times)}
    return [seen.get(index, []) for index in _index(path, times)]


def subtitle_lines(times: list[float], reads: list[list[str]]) -> list[tuple[float, float, list[str]]]:
    """(from, to, words) of each subtitle GTA showed: the same line read on consecutive frames is one."""
    from collections import Counter

    lines: list[list[Any]] = []  # [from, to, readings]
    showing = False
    for t, read in zip(times, reads):
        if not read:
            showing = False
            continue
        seen = set(lines[-1][2][0]) if showing else set()
        if showing and len(set(read) & seen) >= 0.4 * len(set(read) | seen):
            lines[-1][1] = t
            lines[-1][2].append(read)
        else:
            lines.append([t, t, [read]])
            showing = True
    out = []
    for start, end, readings in lines:
        # OCR noise differs frame to frame; the clean reading repeats
        counts = Counter(tuple(r) for r in readings)
        top = max(counts.values())
        out.append((start, end, list(max((r for r, c in counts.items() if c == top), key=len))))
    return out


def readable(lines: list[tuple[float, float, list[str]]]) -> list[tuple[float, float, list[str]]]:
    """The subtitle lines that are text: GTA holds a line for a second or more, so a read seen on one
    frame only is OCR noise on headlights or signs ("rrkrkkkk rrr"), and so are lines of non-words."""
    import re

    def word(token: str) -> bool:
        return bool(re.search(r"[aeiouy]", token)) and not re.search(r"(.)\1\1", token) and len(token) <= 14

    return [(a, b, line) for a, b, line in lines
            if b - a >= 0.5 / SUBTITLE_HZ and line and sum(map(word, line)) >= 0.6 * len(line)]


def scene_frames(path: Path, times: list[float], width: int = 640) -> list[bytes]:
    """JPEG frames of the source at ``times`` (in order), for Claude to see the scene."""
    import cv2

    seen = {}
    for index, frame in _frames(path, times):
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
        seen[index] = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
    return [seen[i] for i in _index(path, times) if i in seen]


def _subtitle_times(words: list[dict[str, Any]]) -> list[float]:
    first, last = min(float(w["start"]) for w in words), max(float(w["end"]) for w in words)
    return [float(t) for t in np.arange(max(0.0, first - 0.5), last + 0.5, 1 / SUBTITLE_HZ)]


def label_by_subtitles(clip: Path, words: list[dict[str, Any]],
                       lines: Optional[list[tuple[float, float, list[str]]]] = None) -> list[dict[str, Any]]:
    """Cat Clips 3.0: the words said while GTA shows a subtitle they match are the game's, the whole line
    of them (the transcript often mishears the game); every other word is the streamer's."""
    from difflib import SequenceMatcher

    if not words:
        return []
    if lines is None:
        times = _subtitle_times(words)
        lines = subtitle_lines(times, subtitle_tokens(clip, times))
    texts = [(_token_list(str(w.get("text", ""))) or [""])[0] for w in words]
    roles = ["streamer"] * len(words)
    for start, end, line in lines:
        # frames are 1/3 s apart and the transcript's timing is loose by over half a second
        window = [i for i, w in enumerate(words) if start - 1.0 <= float(w["start"]) <= end + 1.0]
        if not window:
            continue
        said = [texts[i] for i in window]
        pairs = [(block.a + k, block.b + k) for block in SequenceMatcher(None, line, said, autojunk=False)
                 .get_matching_blocks() for k in range(block.size)]
        # a lone common word ("the", "you") is the streamer talking over the subtitle
        if len(pairs) < 2 and not (pairs and len(line[pairs[0][0]]) >= 4 and len(line) <= 3):
            continue
        # the words between the first and last match, plus as many as the line has before/after them
        a = max(0, pairs[0][1] - pairs[0][0])
        b = min(len(window) - 1, pairs[-1][1] + len(line) - 1 - pairs[-1][0])
        for i in window[a:b + 1]:
            roles[i] = "game"
    logger.info("Caption voices by subtitle: %d game, %d streamer words", roles.count("game"), roles.count("streamer"))
    return [{**w, "role": role} for w, role in zip(words, roles)]


def game_subtitles_on(path: Path, times: list[float]) -> list[bool]:
    """Whether the frame at each time shows the game's own subtitles (outlined or plain)."""
    from .source_captions import caption_lines_in_frame

    seen = {index: bool(caption_lines_in_frame(frame)) or plain_subtitle_on(frame)
            for index, frame in _frames(path, times)}
    return [seen.get(index, False) for index in _index(path, times)]


def _audio(clip: Path) -> np.ndarray:
    import subprocess

    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(clip), "-vn", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
        capture_output=True, check=True, timeout=300,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def _calls(clip: Path, words: list[dict[str, Any]]) -> list[tuple[float, float, str]]:
    """GTA's phone calls during the words; none when the card can't be read."""
    try:
        from .call_card import calls

        return calls(clip, min(float(w["start"]) for w in words) - 1.0, max(float(w["end"]) for w in words) + 1.0)
    except Exception as exc:
        logger.warning("Reading GTA's call card failed (%s); no phone lines", exc)
        return []


def label_words(clip: Path, words: list[dict[str, Any]], width: int, height: int,
                face: Optional[tuple[float, float, float, float]] = None,
                keep_game_lines: bool = False, title: str = "") -> list[dict[str, Any]]:
    """Caption words with a ``role``; unsure voices and game lines already subtitled on screen removed.

    ``keep_game_lines`` (Cat Clips 3.0): no line is removed; Claude judges each line (``voice_judge``),
    with ``label_by_subtitles`` as the fallback. The game's own subtitles fall outside the 3.0 crop, so they'd be lost otherwise.

    ``face`` is the webcam face (source pixels) when the layout already found it.
    Any failure returns every word as the streamer.
    """
    plain = [{**w, "role": "streamer"} for w in words]
    if keep_game_lines:
        if not words:
            return []
        try:
            times = _subtitle_times(words)
            lines = readable(subtitle_lines(times, subtitle_tokens(clip, times)))
            # the coloured-word check runs per frame inside subtitle_text; a line-level poison was
            # tried and reverted: the colour detector flickers with club lighting and killed real lines
            fallback = label_by_subtitles(clip, words, lines)
        except Exception as exc:
            logger.warning("Reading the game's subtitles failed (%s); every line is the streamer's", exc)
            lines, fallback = [], plain
        from .voice_judge import apply_verdict, judge, with_subtitles

        calls = _calls(clip, words)
        try:
            motion_at = (lambda ts: mouth_motion(clip, face, ts)) if face is not None else None
            speak_at = None
            if face is not None:
                from .asd import speaking_scores

                speak_at = lambda spans: speaking_scores(clip, face, spans)  # noqa: E731
            return judge(words, _audio(clip), RATE, motion_at, lines, fallback, calls=calls,
                         speak_at=speak_at, title=title, frames_at=lambda ts: scene_frames(clip, ts))
        except Exception as exc:
            logger.warning("Claude couldn't judge the caption voices (%s); keeping the subtitle match", exc)
            # still: every GTA subtitle is a game line, and a game line in a call is the phone
            from .voice_judge import make_timer
            try:
                timer = make_timer(_audio(clip), RATE)
            except Exception:
                timer = None
            return with_subtitles(apply_verdict(words, {}, fallback, set(), calls), lines, [], calls,
                                  timer=timer)
    try:
        segments = speech_segments(words)
        if len(segments) < 2:
            return plain
        audio = _audio(clip)
        group_of: dict[int, int] = {}
        vectors, embedded = [], []
        for i, (start, end, _) in enumerate(segments):
            vector = embed(audio[int(start * RATE):int(end * RATE)])
            if vector is not None:
                vectors.append(vector)
                embedded.append(i)
        if not vectors:
            return plain
        for i, label in zip(embedded, cluster(vectors)):
            group_of[i] = label
        for i in range(len(segments)):  # stretches too short for a voice vector follow the nearest one
            if i not in group_of:
                group_of[i] = group_of[min(embedded, key=lambda j: abs(j - i))]
        if len(set(group_of.values())) == 1:
            return plain
        sequence = [group_of[i] for i in range(len(segments))]
        seconds: dict[int, float] = {}
        for i, (start, end, _) in enumerate(segments):
            seconds[group_of[i]] = seconds.get(group_of[i], 0.0) + end - start
        middles = [(start + end) / 2 for start, end, _ in segments]
        subs_on = game_subtitles_on(clip, middles)
        subs_share: dict[int, float] = {}
        for group in seconds:
            mine = [subs_on[i] for i in range(len(segments)) if group_of[i] == group]
            subs_share[group] = sum(mine) / len(mine)
        mouth_lift = None
        if face is not None:
            motion = mouth_motion(clip, face, middles)
            overall = float(motion.mean()) + 1e-6
            mouth_lift = {
                group: (float(np.mean([motion[i] for i in range(len(segments)) if group_of[i] == group])) - overall) / overall
                for group in seconds
            }
        roles = assign_roles(sequence, seconds, mouth_lift, subs_share)
        streamer_subs = [subs_on[i] for i in range(len(segments)) if roles[group_of[i]] == "streamer"]
        if streamer_subs and sum(streamer_subs) >= 0.5 * len(streamer_subs):
            # "Subtitles" even while the streamer talks: a HUD line that never leaves, not the game's lines.
            subs_on = [False] * len(segments)
            subs_share = {group: 0.0 for group in seconds}
            roles = assign_roles(sequence, seconds, mouth_lift, subs_share)
        logger.info("Caption voices: %s", {g: (roles[g], round(seconds[g], 1)) for g in roles})
        labelled = []
        for i, (_, _, indexes) in enumerate(segments):
            role = roles[group_of[i]]
            if role == "unsure" or (role == "game" and subs_on[i]):
                continue  # owner: leave doubtful voices out; the game already shows its own subtitle
            labelled += [{**words[k], "role": role} for k in indexes]
        return labelled
    except Exception as exc:
        logger.warning("Caption voice roles failed (%s); one caption style", exc)
        return plain
