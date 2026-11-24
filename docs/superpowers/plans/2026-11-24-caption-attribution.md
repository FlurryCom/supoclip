# Caption Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline, as the
> owner chose for the previous Cat Clips features). Steps use checkbox syntax.

**Goal:** captions on the right speaker at the right time, for any live, measured.

**Architecture:** hard evidence first (GTA subtitle OCR, call card, webcam ASD, forced
alignment), fixed fusion rules second, Claude last and only twice-agreed.

**Tech stack:** existing worker venv (torch 2.13 + CUDA, openai-whisper, onnxruntime, cv2,
numpy); Light-ASD vendored (MIT); no image rebuild.

**Spec:** docs/superpowers/specs/2026-11-24-caption-attribution-design.md

## Global constraints

- No per-streamer tuning; no new pip dependencies; weights download at runtime into
  `uploads/models/` (campplus pattern).
- Every failure degrades to today's behaviour, never to a crash (label_words already wraps).
- Tests run offline and fast: model downloads and GPU runs are mocked in unit tests.

### Task 1: `align.py` — a line starts when its voice does

**Files:** create `backend/src/media/align.py`, `backend/tests/unit/test_align.py`.

**Produces:** `speech_onset(audio, rate, start, end) -> float | None` (first sustained
speech-energy onset in the window); `align_text(model_fn, audio, rate, text, start, end)
-> list[(word, start, end)] | None` (whisper forced alignment via
`whisper.timing.find_alignment`, gated: times monotonic, inside window ±0.3 s, total
duration 0.3–1.8× a 160 wpm estimate; None when the gate fails); `line_times(...)` combining
both: aligned word times, else onset + proportional spread, never before onset.

- [ ] Failing tests: onset finds a −20 dB→−5 dB step; silence → None; gate rejects
      non-monotonic/out-of-window alignments (fake model_fn); line_times never before onset.
- [ ] Implement; tests pass.

### Task 2: unheard GTA subtitles use aligned time

**Files:** modify `backend/src/media/voice_judge.py` (`with_subtitles`),
`backend/src/media/voice_roles.py` (pass clip audio through); tests in
`backend/tests/unit/test_voice_judge.py`.

**Consumes:** Task 1 `line_times`.

- [ ] Failing test: an unheard subtitle drawn at 41.5 s whose voice starts at 42.4 s renders
      words from 42.4 s; heard lines keep transcript times.
- [ ] Implement (audio + aligner injected, default real); tests pass.

### Task 3: `asd.py` — is the webcam face speaking?

**Files:** create `backend/src/media/asd.py` (vendored Light-ASD net + vendored
python_speech_features mfcc, both MIT with attribution), `backend/tests/unit/test_asd.py`.

**Produces:** `ensure_weights() -> Path | None` (downloads `finetuning_TalkSet.model`);
`speaking_scores(clip, cam_face, spans, rate=16000) -> list[float] | None` (mean per-span
score; None when no cam/weights/failure).

- [ ] Failing tests: model forward on random (T×13 MFCC, T/4×112×112 video) returns per-frame
      scores; `speaking_scores` averages spans and returns None without a face.
- [ ] Implement; tests pass (CPU, random weights — no download in tests).

### Task 4: fusion rules + two-pass Claude

**Files:** modify `backend/src/media/voice_judge.py`, `voice_roles.py`; tests in
`test_voice_judge.py` / `test_voice_roles.py`.

**Consumes:** Task 3 scores (`facts["speaking"]`), existing subtitle/call facts.

- [ ] Failing tests: span with speaking ≥ 0.75 → streamer even when Claude says npc; span
      ≤ 0.25 with cam → game (voice from Claude); no cam → Claude's call stands; two Claude
      passes disagreeing on a span → rule fallback; agreeing → kept; GTA subtitle still
      overrides everything.
- [ ] Implement: `judge` takes `speaking` scores, prompt gains the ASD line, runs Claude
      twice (second pass only when the first leaves rule-free spans ambiguous… no: always
      twice, temperature differs only by sampling); merge = agreement or fallback.
- [ ] Tests pass.

### Task 5: gabarito + bench

**Files:** create `backend/tools/caption_bench.py`, `docs/superpowers/gabarito/` JSONs,
owner review artifact (out of repo).

- [ ] Bench: given gabarito JSON `{video, sentences: [{start, end, text, speaker}]}` and a
      labelling function, print per-speaker word accuracy, confusion pairs, and lines
      starting >0.3 s before gabarito start; unit test with a toy gabarito.
- [ ] Pre-label the three clips with the new pipeline; build the artifact review page with
      per-sentence audio snippets; owner corrects; freeze gabarito.
- [ ] Measure old vs new; iterate thresholds until goals met (≥95% words, no early lines).

### Task 6: ship

- [ ] Full test suite for touched files; commit per task (secrets grep before each push).
- [ ] Re-render the three clips; frame-check; send to the owner in PT-BR with the bench table.
