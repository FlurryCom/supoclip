"""Grades caption speaker attribution against a human gabarito (Cat Clips 3.0).

Gabarito: {"<clip>": {"src": "<file>", "linhas": [[start, end, text, speaker, sure], ...]}} with
speaker in xqc | lucia | npc | fone | lixo. Run inside the worker:

    /app/.venv/bin/python -m src.tools.caption_bench /tmp/gabarito.json

Spec: docs/superpowers/specs/2026-11-24-caption-attribution-design.md.
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any

EARLY = 0.5  # a caption word starting this much before its line's voice is "early"


def spoken(word: dict[str, Any]) -> str:
    voice = word.get("voice")
    if word.get("role") != "game":
        return "xqc"
    return {"protagonist": "lucia", "phone": "fone"}.get(voice, "npc")


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.lower()))


def assign(word: dict[str, Any], lines: list[list], tol: float = 1.0) -> int | None:
    """The gabarito line this caption word belongs to: nearest in time, text match breaking ties."""
    mid = (float(word["start"]) + float(word["end"])) / 2
    token = _tokens(str(word["text"]))
    best, best_key = None, None
    for k, (a, b, text, _speaker, _sure) in enumerate(lines):
        gap = max(a - mid, mid - b, 0.0)
        if gap > tol:
            continue
        overlap = 1 if token & _tokens(text) else 0
        key = (-overlap, gap)
        if best_key is None or key < best_key:
            best, best_key = k, key
    return best


def grade(words: list[dict[str, Any]], lines: list[list]) -> dict[str, Any]:
    per_line: list[list[dict]] = [[] for _ in lines]
    stray = []
    for word in words:
        k = assign(word, lines)
        (per_line[k] if k is not None else stray).append(word)
    right = wrong = 0
    confusion: dict[tuple[str, str], int] = {}
    missed, early = [], []
    by_line = [{"right": 0, "wrong": 0} for _ in lines]
    for k, (a, b, text, speaker, _sure) in enumerate(lines):
        mine = per_line[k]
        if speaker == "lixo":
            for word in mine:  # music or noise: captioning it at all is the mistake
                wrong += 1
                by_line[k]["wrong"] += 1
                confusion[("lixo", spoken(word))] = confusion.get(("lixo", spoken(word)), 0) + 1
            continue
        if not mine:
            missed.append(text)
            continue
        for word in mine:
            got = spoken(word)
            if got == speaker:
                right += 1
                by_line[k]["right"] += 1
            else:
                wrong += 1
                by_line[k]["wrong"] += 1
                confusion[(speaker, got)] = confusion.get((speaker, got), 0) + 1
        if min(float(w["start"]) for w in mine) < a - EARLY:
            early.append(text)
    return {"right": right, "wrong": wrong, "by_line": by_line,
            "accuracy": round(right / max(1, right + wrong), 3),
            "confusion": {f"{a}->{b}": n for (a, b), n in sorted(confusion.items(), key=lambda kv: -kv[1])},
            "missed_lines": missed, "early_lines": early, "stray_words": len(stray)}


def main() -> None:
    from pathlib import Path

    from src.media.catclips3 import plan_catclips3
    from src.media.voice_roles import label_words

    gabarito = json.loads(Path(sys.argv[1]).read_text())
    cache = Path("/tmp/bench_labels.json")
    labels = json.loads(cache.read_text()) if "--reuse" in sys.argv and cache.exists() else {}
    totals = {"right": 0, "wrong": 0, "sure_right": 0, "sure_wrong": 0}
    for clip_name, info in gabarito.items():
        clip = Path("/app/uploads/uploads") / info["src"]
        cache = json.loads(Path(str(clip).replace(".mp4", ".transcript_cache.json")).read_text())
        end = max(b for _a, b, *_ in info["linhas"]) + 2.0
        words = [{"text": w["text"], "start": w["start"] / 1000, "end": w["end"] / 1000}
                 for w in cache["words"] if w["end"] / 1000 <= end]
        if clip_name in labels:
            labelled = labels[clip_name]
        else:
            face = plan_catclips3(clip, 1920, 1080, "x")[3]
            labelled = label_words(clip, words, 1920, 1080, face, keep_game_lines=True,
                                   title=info.get("titulo", ""))
            labels[clip_name] = labelled
        report = grade(labelled, info["linhas"])
        sure = {"right": sum(c["right"] for c, l in zip(report["by_line"], info["linhas"]) if l[4]),
                "wrong": sum(c["wrong"] for c, l in zip(report["by_line"], info["linhas"]) if l[4])}
        sure["accuracy"] = sure["right"] / max(1, sure["right"] + sure["wrong"])
        totals["right"] += report["right"]
        totals["wrong"] += report["wrong"]
        totals["sure_right"] += sure["right"]
        totals["sure_wrong"] += sure["wrong"]
        print(f"== {clip_name}: {report['accuracy'] * 100:.0f}% "
              f"({report['right']}/{report['right'] + report['wrong']} palavras; "
              f"linhas comprovadas: {sure['accuracy'] * 100:.0f}%)")
        for pair, n in report["confusion"].items():
            print(f"   {pair}: {n}")
        for text in report["missed_lines"]:
            print(f"   perdida: {text[:60]}")
        for text in report["early_lines"]:
            print(f"   adiantada: {text[:60]}")
        if report["stray_words"]:
            print(f"   fora do gabarito: {report['stray_words']} palavras")
    cache.write_text(json.dumps(labels))
    share = totals["right"] / max(1, totals["right"] + totals["wrong"])
    sure_share = totals["sure_right"] / max(1, totals["sure_right"] + totals["sure_wrong"])
    print(f"== total: {share * 100:.1f}% ({totals['right']}/{totals['right'] + totals['wrong']}) | "
          f"comprovadas: {sure_share * 100:.1f}% ({totals['sure_right']}/{totals['sure_right'] + totals['sure_wrong']})")


if __name__ == "__main__":
    main()
