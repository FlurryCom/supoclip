# Cat Clips kit: live clips with their own edit

Status: approved by the owner on 2026-11-19 (option A, chat on screen, switchable kits).

## Why

Live clips go out to TikTok, YouTube Shorts and Instagram (Cat Clips). Today they
use SupoClip's default look (yellow word-pop captions, emojis, hook title on top),
the same look every SupoClip user gets. Platforms push down reposted, unedited
content, and a generic look reads as "just a cut of someone's stream". The goal is
a recognizable edit of our own, built from what MotionMoon already has, without
slowing the pipeline.

## Goals

- A clip from Cat Clips is recognizable at a glance: own caption style, own end tag.
- Show real chat reacting on screen at the moment it blew up (owner: "Sim, mostra o chat").
- Sound effects on the cut and on the chat peak, from MotionMoon's CC0 library.
- No measurable render slowdown and no GPU use (Whisper keeps the GPU).

## Non-goals

- Running MotionMoon/Remotion (option B, rejected by the owner: too slow and heavy).
- Music detection and replacement (separate piece of work, see "Later").
- Changing how uploads made by hand in SupoClip look; only live clips get the kit.

## Anti-goals

- Hateful or doxxing chat text burned into a posted video.
- Chat usernames on screen (private individuals; text only).
- Breaking the 61 s minimum: the end tag sits inside the clip, it adds no time.

## Approaches considered

- **A. Rebuild the look inside SupoClip's ffmpeg/ASS render (chosen by the owner).**
  Captions, titles, chat and end tag are ASS subtitle events in the same burn pass
  that exists today; sound effects are one extra `amix` input. Cost: near zero.
- **B. Render each clip through MotionMoon's Remotion.** Richer animation, about
  3.5 min of GPU per clip, Node + Chrome in the worker. Rejected.

## Identity (from the owner's logo, `backend/assets/brand/catclips/`)

- Colors: yellow `#FEC803`, black `#111111`, cream `#F3E9D2` (the logo's line art).
- Type: the logo is a bold hand-drawn marker in caps with a cream offset shadow.
  Captions use the closest open-license hand font (Google Fonts, OFL), downloaded
  into `backend/fonts/` with its license; Bangers is the fallback.
- Mascot: the black cat with the camera (`avatar.jpg`), used on the end card.

## Kits are switchable (owner: "não vai ser pra sempre ... quando a gente quiser alternar")

The Cat Clips edit is one **edit kit**, not a hard-coded look. A kit bundles
everything below (caption template, chat overlay on/off, sound effects, end card).

- `backend/src/edit_kits.py` holds the registry: `none` (SupoClip's normal look,
  today's behavior) and `catclips`. A new kit later is one more entry plus its
  assets folder, with no pipeline changes.
- Each live channel has an `edit_kit` setting (column on `live_channels`,
  default `none`), picked from a dropdown on the channel card in the Live page,
  next to the score and chat sliders. The current channels are switched to
  `catclips`.
- The render looks up the window's channel kit (the same lookup the score gate
  already does) and applies it. Switching a channel's kit affects the next
  windows; clips already rendered keep their look.

## Design

1. **Cat Clips caption template.** A new entry in `caption_templates.py`
   (`"catclips"`), same fields as the others: caps, karaoke with word pop, no
   emojis, cream words with a thick black outline, the active word in black on a
   yellow pill (`word_box`). The hook title follows the template: brand yellow
   with a black outline (no new title code). The
   live recorder sends
   `caption_template` (env `LIVE_CAPTION_TEMPLATE`, default `catclips`) when it
   creates a task. Hook title stays on, restyled by the template.

2. **Chat messages are stored.** The recorder already reads every chat line to count
   chatters. It now also keeps a sample of message texts in a `messages` jsonb
   column on `live_chat_buckets` (at most 20 per 10 s bucket, text only, no
   author), so they are deleted with the buckets. Dropped at write time: links, @mentions, lines over 60
   characters, and lines that hit a small blocklist (slurs, PT and EN). Deleted with
   the recording's other data.

3. **The gate picks the peak.** `live_scoring.gate_live_segments` already loads the
   chat buckets for a live window. For each kept segment it also finds the hottest
   bucket inside it and attaches `live_overlay = {peak_s, chat: [{t, text}]}`
   (clip-relative seconds, up to 8 messages from the peak's ±5 s, in order). No
   chat data (old recordings, YouTube lag): no overlay, the rest of the kit still
   applies.

4. **Chat on screen.** The caption burn adds ASS events: each message pops in as a
   small rounded black bubble with cream text and a yellow edge, stacked on the left third of the frame, 0.4 s apart, starting
   at `peak_s`, each visible about 2.5 s. Kept clear of the caption and title bands
   (the existing `caption_center_clear_of_band` logic).

5. **Sound effects.** Two CC0 sounds copied from MotionMoon into
   `backend/assets/sfx/` with their license lines: `cartoon-whoosh` at 0 s and
   `sfx-golpe-abertura` at `peak_s`, at the
   levels the MotionMoon catalog lists, mixed under the original audio.

   Chat, sound effects and end card are applied in one extra ffmpeg pass on the
   finished clip (`apply_edit_kit`), so the existing render stays untouched. That
   pass costs one re-encode of a ~75 s clip (seconds, CPU only); if it fails the
   clip is kept without the kit, never lost.

6. **End card.** The last 1.5 s: the yellow card slides over the video with the
   cat mascot and "CAT CLIPS" plus a "Siga pra mais" line (one ffmpeg `overlay`
   of a prebuilt PNG card, with a fade). Inside the clip length.

## Later (not in this piece)

- Music: detect radio music with PANNs before render and drop or score down clips
  that carry it.
- Per-channel kit settings in the Live page.

## Left to the builder

- Exact ASS styling, bubble shape and timings, tuned on real clips.
- Blocklist contents (start small, extend from what shows up).
- Tests: the message filter, the peak pick and the overlay timing are pure
  functions with unit tests; one render smoke test on a short sample.

## Revision 2 (owner feedback on the first real clip, 2026-11-19)

The owner found the hand font hard to read, didn't like the "CAT CLIPS" end
card, and asked for platform CTAs and the usual short-form sounds.

- **Font:** Archivo Black (picked from four options rendered on a real frame), for
  captions and chat bubbles.
- **End card removed.** In its place, a **per-platform CTA** over the last 2.5 s,
  with no Portuguese words (button labels in English are fine):
  - TikTok: a mouse pointer slides to the cat avatar and taps the red "+", which
    turns into a check.
  - YouTube Shorts: the pointer taps a white "Subscribe" pill, which turns into a
    grey "Subscribed" pill with a bell that wiggles.
  - Instagram: the pointer taps a "Follow" pill, which turns into "Following".
  The button sits in a dark rounded card in the lower middle of the frame (the
  safe zone), not where the app's own button is, so it never doubles up with
  the real UI.
- **Where the CTA is applied:** at posting time, in the recorder, per target
  provider. The clip in the SupoClip library has no CTA; each platform gets its
  own variant. If adding the CTA fails, the clip is posted without it. Only
  channels whose kit isn't `none` get a CTA; the CTA files belong to the kit
  (`live/assets/cta/<kit>/<provider>.mov`).
- **Animations:** generated once by a script (Pillow frames at 2x for smooth edges,
  encoded by ffmpeg into a PNG-codec .mov with alpha and its own click/pop/ding
  audio track). Committed as assets; no Node or Remotion at runtime.
- **Sounds:** a notification bell plus the whoosh at 0 s, a deep boom on the chat
  peak (replacing the hit), and click, pop and bell ding inside the CTAs. All of
  them are synthesized by our own script (no third-party audio, so no copyright
  claims). The original Vine boom is not used.
- Chat bubbles end before the CTA (the kit's tail is now `cta_seconds = 2.5`).
