# Caption attribution that doesn't guess (Cat Clips 3.0)

Owner's complaint (2026-11-24): captions put game lines on the streamer and vice versa
("What the hell, Barry" shown as the streamer; "What the fuck, yo, bro" shown as an NPC),
and captions appear 1–2 s before the voice. "We need a study of how to do this properly;
we're winging it."

## Intent

Every caption lands on the right speaker at the right time, for any live (not tuned to one
streamer), with Claude reasoning over reliable evidence instead of guessing from text.

## Why today's pipeline errs

- With no GTA subtitle on screen, Claude picks the speaker from the transcript text alone;
  the signals we hand it are weak. Measured on the owner's three test clips:
  - per-stretch voice prints give a different letter to almost every sentence (short,
    overlapped speech — the known weak spot of embedding clustering);
  - the webcam "mouth motion" measure is frame differencing: head movement counts as talking
    (1.87× on "Barry!", which an NPC says);
  - relative loudness collapses when the streamer yells over loud game audio.
- A GTA subtitle Whisper never heard is drawn at the time GTA *displays* it, and GTA displays
  1–2 s before the voice starts.

## Goals

- ≥95% of caption words on the right speaker across the three gabarito clips.
- No caption line starting before its voice (tolerance 0.3 s).
- Generic: no per-streamer training or tuning.
- Measured: a bench script scores any pipeline against a labelled gabarito before it ships.

## Non-goals

- Lip-sync for in-game characters (only the webcam gets ASD).
- Perfect NPC identity names; numbering NPCs apart is enough.
- Languages beyond the streamer's (alignment uses the clip's language via Whisper).

## Approaches considered

1. **More prompt engineering on today's signals** — rejected: the signals themselves are
   wrong (see above); Claude can't fix evidence that lies.
2. **Per-streamer voice enrollment** — rejected by the owner ("não é um estudo do xQc");
   also operationally heavy (enrollment per channel).
3. **Hard evidence first, Claude last** — chosen: add two strong generic signals (webcam
   active-speaker detection; word-to-audio forced alignment), fuse with the OCR facts we
   already trust (GTA subtitles, call card) through fixed rules, and let Claude decide only
   what the rules can't, in two passes (only agreement counts).

## Decisions

- **ASD (who is talking on cam):** Light-ASD (CVPR'23, MIT), `finetuning_TalkSet.model`
  (4.2 MB, tuned for in-the-wild video), vendored model code + runtime weight download into
  `uploads/models/asd/` (same pattern as the campplus voice model). Input: 25 fps grayscale
  112×112 crops of the webcam face box we already detect + 13-MFCC audio (python_speech_features'
  mfcc vendored for feature parity, MIT). Output: per-frame "this face is speaking" score,
  averaged per speech stretch. torch 2.13 + GPU are already in the worker image: no rebuild.
- **Timing (when a line starts):** forced alignment with the already-installed openai-whisper
  (`whisper.timing.find_alignment` on the known text over the line's audio window). Sanity
  gates (monotonic, inside window, sane duration); fallback to speech-energy onset; a line
  never starts before the detected voice.
- **Fusion rules (no AI):**
  - words matching a GTA subtitle → game (exists);
  - call card up and the game voice isn't the protagonist → phone (exists);
  - ASD says the webcam face speaks (score ≥ high) → streamer;
  - ASD says it doesn't (score ≤ low, cam visible) → game;
  - between thresholds, or no cam → Claude.
- **Claude, two passes:** judges only speaker identity the rules leave open (protagonist vs
  npc vs phone, which npc) with the ASD score and aligned subtitles in the prompt. Run twice;
  where the passes disagree, the rule-based fallback wins.
- **Gabarito + bench:** `tools/caption-bench` labels the three test clips offline and prints
  per-speaker word accuracy + timing violations; gabarito JSON reviewed by the owner on an
  artifact page with per-sentence audio snippets. The new pipeline ships only when it beats
  the goals; the bench stays for the next regression.

## Left to the builder

Thresholds (ASD high/low, alignment sanity), MFCC vendoring details, how face crops are read
(reuse the clip decode pass), bench output format.

## Known caveats

- Pretrained ASD weights come from research datasets (AVA/TalkSet); repos are MIT but weight
  terms are murkier — same situation as every ASD deployment; noted, not blocking (weights
  are downloaded at runtime, not committed).
- When the webcam is occluded or absent, streamer-vs-game falls back to rules + Claude as today.
