# Blur nudity and gore in live clips before posting

Status: approved by the owner on 2026-11-19 (approach C: NudeNet + Claude, switches on accounts and channels).

## Why

GTA 6 streams carry nudity (strip clubs, NPCs) and gore. Clips auto-post to
TikTok, YouTube Shorts and Instagram through Postiz, where that content gets
videos removed, age-restricted or demonetized. The owner wants a quick,
automatic check that blurs only the affected part of the frame, with switches
to turn it off where it isn't needed.

## Goals

- Find nudity and heavy gore in each live clip and blur only that region,
  only for those seconds.
- Analyse once per clip; blur per posting account, at posting time.
- Switches on both sides (owner's words: "se o canal tá com o seletor
  habilitado, os canais da Twitch já ficam habilitados por padrão, e eu tenho a
  liberdade de escolher qual canal eu quero desabilitar"):
  - each posting account (TikTok, YouTube, Instagram) has **"Borrar nudez e gore"**,
    on by default;
  - each followed live channel (Twitch or YouTube streamer) has the same switch,
    on by default.
  - A clip is blurred for an account only when **both** the account and the
    clip's live channel have it on. Analysis runs only for live channels that
    have it on.
- Fast and light: runs on this PC (no new paid services). The analysis adds
  seconds, not minutes, per clip.

## Non-goals

- Ordinary game blood: bullet hit sprays, small splatter. Only heavy gore counts
  (owner chose "só gore pesado"): pools of blood, open wounds, dismemberment,
  executions shown up close.
- Music detection (separate future work).
- Blurring the clip stored in SupoClip: the library keeps the original.

## Anti-goals

- A clip so blurred it's unwatchable gets posted: if blurred seconds exceed
  40% of the clip for an account, that clip is **not posted** to that account
  (recorded as refused with the reason). It stays in SupoClip.
- Posting fails because of the check: any error in analysis or blurring posts
  the clip without blur only if the account has the switch off; if the
  switch is on, a failed blur means **don't post** (fail closed, since the
  point is safety).

## Approaches considered

- **A. Claude only.** Contact sheets of frames to Claude (subscription, via
  the CLI, which accepts images: tested, ~2 s per call with Haiku). It finds
  nudity and gore, with regions as cells of a 4x4 grid. It's simple, but the
  blur is coarse, and small or distant nudity can be missed.
- **B. NudeNet only.** A local open-source nudity detector (ONNX, about
  100 MB, CPU) returns exact boxes per frame. It's precise for nudity, but it
  can't see gore.
- **C. Both (chosen by the owner).** NudeNet for nudity (exact boxes), and
  Claude for heavy gore (grid cells).

## Design

1. **Settings.** Migration `20261119_0009_live_sensitive_blur.sql` (numbered
   after the other session's 0007/0008):
   - `live_channels.blur_sensitive boolean NOT NULL DEFAULT true`;
   - `live_post_targets.blur_sensitive boolean NOT NULL DEFAULT true`.
   Both are validated in the existing settings routes. Each gets a switch on the
   Live page: on the channel card next to "Kit de edição", and on each account
   card in "Postagem automática".

2. **Analysis (worker, after the clip is rendered with its kit).** Only for
   live-window segments whose channel has `blur_sensitive` on.
   - Sample 2 frames per second.
   - **Nudity:** NudeNet on every sampled frame. The exposed classes count
     (exposed breasts, buttocks, genitalia, anus), at confidence ≥ 0.45. Each
     box is padded 15% and merged across neighbouring samples into a region:
     `{kind: "nudity", start, end, x, y, w, h}`, in clip seconds and fractions of
     the frame.
   - **Gore:** one Claude call (Haiku, via the existing `claude-cli` path, with
     images through `--input-format stream-json`). It gets the samples at 1 fps
     as contact sheets of 16 tiles with timestamps, and is asked for heavy gore
     only, by tile number and 4x4 grid cells. It returns JSON validated against
     a schema, and the cells become regions with `kind: "gore"`.
   - The regions are saved in a new table, `live_clip_sensitive(file_path
     PRIMARY KEY, duration, regions jsonb, checked_at, error)`, keyed by the
     rendered clip's file path. This is a builder call: `workflow_meta` is only
     written for planned workflow clips, and reusing it would make
     regeneration treat live clips as workflow clips. The recorder joins it
     through `generated_clips.file_path`.
   - The Claude gore call is retried twice. If it still fails, the row keeps
     the nudity regions plus `error` set, and the clip counts as **not
     checked**.

3. **Blur (recorder, at posting time).** For each post target, the recorder
   blurs when `target.blur_sensitive AND channel.blur_sensitive` and the clip
   has regions.
   - It is the same ffmpeg pass that adds the CTA: for each region, a strong
     `boxblur` on a crop of that box, overlaid back with
     `enable='between(t,start,end)'`.
   - If the blurred seconds are over 40% of the clip, the post is refused for
     that account.
   - If blurring is required but the clip is not checked (no row, `error` set,
     or the downloaded clip's duration differs from the analysed one by more
     than 0.2 s because it was trimmed or regenerated after the analysis), the
     clip is **not posted** to that account. The refusal is recorded with the
     reason, so it shows in the Live page; turning the switch off posts the
     next clips unblurred.
   - **Rollout (builder call):** clips rendered before this feature went live
     have no row and are posted as before, as long as they were created before
     the migration's timestamp (`live_sensitive_since`, recorded by the
     migration). Only clips rendered after it need the check.

4. **Logs and visibility.** Each analysed clip logs its counts (nudity regions,
   gore regions, blurred seconds). Refused posts show the reason in the Live
   page's post status, as they do today.

## Known limits

- NudeNet is trained on real photos. Its threshold (0.45) and padding (15%) are
  starting values, not yet measured on GTA footage; they get tuned on the first
  real clips that contain nudity. Until then, detection quality is unverified.
- The gore check needs the Claude CLI in the worker (already installed here).
  If the CLI is unavailable, every clip from a channel with the switch on is
  refused for accounts with the switch on (visible in the Live page).

## Left to the builder

- NudeNet version and model size (the smallest that still finds the classes at
  320–640 px), and whether it runs in the worker or the ai-worker container.
- How regions merge across time (gap tolerance about 1 s).
- Blur strength (strong enough that nothing is recognisable).
- Tests: the region merge, the 40% rule, fail-closed posting and the ffmpeg
  arguments are pure functions with unit tests, plus one smoke test on a
  synthetic clip.
