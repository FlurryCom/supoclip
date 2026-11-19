# Cat Clips Kit Revision 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Cat Clips kit readable and platform-native: Archivo Black captions and bubbles, synthesized intro, peak and CTA sounds, no end card, and a per-platform animated follow/subscribe CTA added at posting time.

**Architecture:**
- The backend kit stays the same shape, minus the end card: it gets a `cta_seconds` tail and new sound files.
- A generator script draws the three CTA animations once, as PNG-codec `.mov` files with alpha and their own audio.
- The recorder, which already downloads each clip before uploading it to Postiz, overlays the CTA that matches the target's provider in one ffmpeg run. On failure it falls back to the plain clip.

**Tech Stack:** Python 3.12, numpy + wave (sound synthesis), Pillow, ffmpeg (PNG-in-MOV with alpha, overlay, amix), psycopg (recorder).

**Spec:** `docs/superpowers/specs/2026-11-19-cat-clips-kit-design.md` (section "Revision 2")

## Global Constraints

- Font family `ArchivoBlack-Regular` (file `backend/fonts/ArchivoBlack-Regular.ttf`, already in the repo) for the `catclips` caption template and the chat bubbles.
- No end card. `KITS["catclips"]["end_card"] is None`, and `cta_seconds = 2.5` reserves the tail: bubbles end by `duration - 2.5`.
- Sounds: `bell.wav` and `cartoon-whoosh.wav` at 0 s, `boom.wav` at the peak. The CTA `.mov` files carry click, pop and (YouTube only) ding. All of them come from our own synthesis script; no third-party audio.
- CTA: 2.5 s, 30 fps, 1080x1920, alpha. No Portuguese words. Labels: TikTok none (a "+" that becomes a check), YouTube `Subscribe` → `Subscribed`, Instagram `Follow` → `Following`. The card sits at y ≈ 0.62 of the height, centred.
- CTA files live at `live/assets/cta/catclips/{tiktok,youtube,instagram}.mov`. Provider `instagram-standalone` uses `instagram.mov`. A channel with kit `none`, or a missing file, gets no CTA.
- Posting never fails because of the CTA: on any error, the plain clip is uploaded.
- Backend tests: `scripts/backend-test.sh <paths>`. Recorder tests: `docker run --rm -v ./live:/app:z --entrypoint sh supoclip-live-recorder -c "pip install -q pytest >/dev/null; cd /app && python -m pytest -q tests"`.

## Review Focus

1. A clip shorter than the CTA (under 2.5 s, which can't happen with the 61 s floor but can on a bad file): the CTA is skipped and the plain clip is posted. Test `test_add_cta_falls_back_on_short_or_broken_clip` (Task 3).
2. A clip without audio: the CTA's sounds still mix in. Test `test_add_cta_on_silent_clip` (Task 3).
3. A provider string we don't know (e.g. `x`, `linkedin`): no CTA, plain clip. Test `test_cta_file_for_unknown_provider_is_none` (Task 3).
4. A clip whose size isn't 1080x1920 (720x1280 after a resolution cap): the CTA is scaled to the clip. Test `test_add_cta_scales_to_the_clip` (Task 3).
5. Bubbles and the CTA never overlap in time. Test `test_chat_events_end_before_cta` (Task 1).

---

### Task 1: Archivo Black, synthesized sounds, no end card

**Files:**
- Create: `backend/assets/sfx/make_sfx.py`, writing `bell.wav`, `boom.wav`, `click.wav` and `pop.wav` into `backend/assets/sfx/`; add them to `LICENSES.md` as "synthesized by make_sfx.py, CC0"
- Modify: `backend/src/edit_kits.py`, `backend/src/caption_templates.py` (`catclips` `font_family`), `backend/src/media/edit_kit_render.py` (`BUBBLE_FONT`, end card optional, tail = `cta_seconds`)
- Delete: nothing (`end_card.png` and `make_end_card.py` stay in git history; remove them from the tree)
- Test: `backend/tests/unit/test_edit_kits.py`, `backend/tests/unit/test_edit_kit_render.py`

**Interfaces:**
- Produces:
  - `KITS["catclips"]`: `end_card None`, `cta_seconds 2.5` (replaces `end_card_seconds`), and `sfx`:
    - `bell.wav` at start, volume 0.6;
    - `cartoon-whoosh.wav` at start, volume 0.5;
    - `boom.wav` at peak, volume 0.8.
  - `KITS["none"]["cta_seconds"] == 0`.
  - `chat_events(..., tail_seconds)`: the last parameter is renamed from `end_card_seconds`.
  - `kit_ffmpeg_args` adds no image input and no `overlay` when `kit["end_card"] is None`.
  - `make_sfx.py`: `bell()`, `boom()`, `click()`, `pop()`, each returning a float32 numpy array at 48 kHz, plus `write_wav(path, samples)`.

- [ ] **Step 1: Update the tests first.**
  - In `test_edit_kits.py`:
    - assert `get_kit("catclips")["end_card"] is None` and `cta_seconds == 2.5`;
    - assert the sfx `(file name, at)` pairs are `[("bell.wav","start"),("cartoon-whoosh.wav","start"),("boom.wav","peak")]`;
    - assert the `catclips` template `font_family == "ArchivoBlack-Regular"`.
  - In `test_edit_kit_render.py`:
    - `test_chat_events_end_before_cta`: with `peak_s=68` and `duration=70`, every event ends at or before 67.5.
    - `test_kit_args_without_end_card_have_no_overlay`: no `overlay` and no `.png` in the args.
    - Update the old end-card assertions.
  - Add `test_sfx_files_are_short_and_loud_enough`. Each synthesized wav is 48 kHz mono, at most 2.5 s long except the boom (at most 3 s), and peaks between 0.3 and 1.0.
- [ ] **Step 2: Run** `scripts/backend-test.sh tests/unit/test_edit_kits.py tests/unit/test_edit_kit_render.py`. Expected: the new assertions FAIL.
- [ ] **Step 3: Implement.**
  - **`make_sfx.py`** (numpy only):
    - bell: inharmonic partials 1, 2.76, 5.40 and 8.93 × 1200 Hz with exponential decays of 1.2/0.8/0.5/0.3 s, 1.6 s long;
    - boom: a sine sweep from 75 to 38 Hz over 1.2 s, plus a little soft-clipped second harmonic and a 1.5 s decay, 2.5 s long;
    - click: 6 ms of filtered noise with a sharp attack;
    - pop: a sine falling from 900 to 300 Hz over 60 ms with a fast decay.
    - Normalize the peaks to 0.9 and write 16-bit wavs. Run it and commit the wavs.
  - **The kit:** apply the changes. Switch the template font. Make `BUBBLE_FONT = "ArchivoBlack-Regular"`.
  - **`kit_ffmpeg_args`:** with no end card, map `[0:v]` (plus subtitles) straight to `[v]`, and drop the `-loop`/image input. The sound input indices start at 1.
  - **Old card files:** remove `end_card.png` and `make_end_card.py` from `backend/assets/brand/catclips/`. Keep `avatar.jpg`, which Task 2 uses.
- [ ] **Step 4: Run** the same tests, then the whole kit set (`test_live_scoring`, `test_live_routes`, `test_edit_kits`, `test_edit_kit_render`). Expected: PASS.
- [ ] **Step 5: Commit** `feat(kits): Archivo Black, synthesized sounds, no end card`.

### Task 2: CTA animation generator

**Files:**
- Create: `live/tools/make_cta.py`
- Create (generated, committed): `live/assets/cta/catclips/{tiktok,youtube,instagram}.mov`
- Test: `live/tests/test_cta_assets.py`

**Interfaces:**
- Consumes: `backend/assets/brand/catclips/avatar.jpg`, `backend/fonts/ArchivoBlack-Regular.ttf`, and `backend/assets/sfx/{click,pop,bell}.wav` (from Task 1).
- Produces: three `.mov` files, each with video codec `png`, pixel format `rgba`, 1080x1920, 30 fps, 75 frames, and an AAC or PCM audio track 2.5 s long.

- [ ] **Step 1: Write the failing test** `test_cta_assets.py::test_each_cta_is_a_short_alpha_movie_with_sound`. For each provider it runs `ffprobe` and checks:
  - the video stream: `codec_name == "png"`, `pix_fmt` contains `a`, `width == 1080`, `height == 1920`, `nb_frames == 75`;
  - there is an audio stream;
  - the duration is 2.5 ± 0.05 s.
- [ ] **Step 2: Run** the recorder tests. Expected: FAIL (no files).
- [ ] **Step 3: Implement `make_cta.py`.** Draw each frame at 2160x3840 in RGBA, downscale with LANCZOS, and write PNGs to a temp folder. Then mux with `ffmpeg -framerate 30 -i %03d.png -i audio.wav -c:v png -pix_fmt rgba -c:a pcm_s16le out.mov`.
  - **Timeline** (seconds):
    - 0.00–0.30: the card scales in from 0.8 to 1.0 with ease-out and alpha 0 → 1. The card is `#111111` at 85% alpha, radius 48, width 0.62·W, centred at y = 0.62·H.
    - 0.30–1.05: the pointer slides from the bottom-right corner off-screen to the button, ease-in-out. The pointer is a white arrow with a black 6 px outline, 110 px tall.
    - 1.05: the pointer presses (scale 0.85 for 0.12 s), and a white ring 40 → 160 px fades out over 0.35 s.
    - 1.10: the button toggles state.
    - 2.20–2.50: everything fades out.
  - **TikTok:** the cat avatar is a 220 px circle in the card centre. Under it sits a red `#FE2C55` 64 px circle with a white "+", which turns into a white check on a grey `#8A8B91` circle at 1.10.
  - **YouTube:** a 120 px avatar circle on the left, and on the right a white pill with black Archivo Black text "Subscribe". At 1.10 it becomes a `#3F3F3F` pill with "Subscribed" in white and a bell glyph that wiggles ±15° for 0.6 s.
  - **Instagram:** a 120 px avatar and a pill with a 4 px white outline reading "Follow". At 1.10 it becomes a filled `#3F3F3F` pill reading "Following".
  - **Audio** (48 kHz mono, 2.5 s): click at 1.05, pop at 1.10, and for YouTube only, bell at 1.30 at volume 0.6.
  - **Run it** for kit `catclips`.
- [ ] **Step 4: Run** the tests, then look at frames 0.2 / 1.0 / 1.4 s of each file: export the frames with ffmpeg on a grey background and read them. Expected: PASS, and the frames show the card, the pointer and the toggled button.
- [ ] **Step 5: Commit** `feat(live): per-platform CTA animations for the Cat Clips kit`.

### Task 3: Recorder adds the platform CTA before upload

**Files:**
- Create: `live/supoclip_live/cta.py`
- Modify: `live/supoclip_live/service.py` (`post_clips`: select `c.edit_kit`, call `cta.with_cta`, then upload the result and delete it in `finally`), `live/Dockerfile` (`COPY assets/ ./assets/`)
- Test: `live/tests/test_cta.py`

**Interfaces:**
- Produces:
  - `CTA_DIR = Path(__file__).resolve().parents[1] / "assets" / "cta"`;
  - `cta_file(kit: str | None, provider: str) -> Path | None`;
  - `cta_args(clip: Path, cta: Path, out: Path, duration: float, has_audio: bool) -> list[str]`;
  - `with_cta(clip: Path, kit: str | None, provider: str) -> Path`. It returns a new file `<clip stem>.<provider>.mp4`, or `clip` itself when there is no CTA or when anything fails.

- [ ] **Step 1: Write the failing tests:**
  - `test_cta_file_for_unknown_provider_is_none`: `cta_file("catclips", "linkedin") is None`, `cta_file("none", "tiktok") is None` and `cta_file("catclips", "instagram-standalone").name == "instagram.mov"`.
  - `test_cta_args_overlay_the_tail`: for a duration of 70, the graph contains `overlay` with `enable='gte(t,67.5)'`, the CTA input has `-itsoffset 67.5`, and there is `amix ... duration=first`.
  - `test_add_cta_scales_to_the_clip`: on a 720x1280 synthetic 5 s clip (made with ffmpeg lavfi, as in the backend smoke test), `with_cta(..., "catclips", "tiktok")` returns a new path with the same size and a duration of 5 ± 0.1 s.
  - `test_add_cta_on_silent_clip`: a clip with no audio comes out with an audio stream.
  - `test_add_cta_falls_back_on_short_or_broken_clip`: a 1 s clip and a garbage file both return the input path unchanged.
- [ ] **Step 2: Run** the recorder tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **Video graph:** `[1:v]scale2ref=w=iw:h=ih[cta][base];[base][cta]overlay=0:0:enable='gte(t,T)':eof_action=pass[v]`, with the CTA input delayed by `-itsoffset T`.
  - **Audio graph:**
    - with clip audio: `[0:a][1:a]amix=inputs=2:normalize=0:duration=first[a]`;
    - without clip audio: `aevalsrc=0:d=D` replaces `[0:a]`;
    - delay the CTA audio with `adelay=T*1000:all=1` instead of relying on `itsoffset` for audio.
  - **Encoding:** `libx264 -preset fast -crf 20 -pix_fmt yuv420p`, `aac 192k`, `-t D`, `+faststart`.
  - **Probing:** use ffprobe with JSON output for the duration, the size and whether there is audio.
  - **Fallback:** skip when the duration is under `cta_seconds + 0.5`.
  - **`post_clips`:**
    - add `c.edit_kit` to the SELECT;
    - after the download: `upload_file = await asyncio.to_thread(cta.with_cta, clip_file, row["edit_kit"], row["provider"])`;
    - upload `upload_file`;
    - in `finally`, delete `upload_file` when it differs from `clip_file`.
- [ ] **Step 4: Rebuild the recorder image (the tests need ffmpeg and the assets), then run** the recorder tests. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(live): add the platform's CTA to each clip before posting`.

### Task 4: Deploy and check

- [ ] **Step 1:** Restart the worker when no task is `processing`, then rebuild and restart `live-recorder`.
- [ ] **Step 2:** Run `with_cta` inside the running recorder container on the newest live clip, downloaded through `backend.download_clip`, once per provider. Export frames at `duration-2.4`, `-1.4` and `-0.9`, and read them.
- [ ] **Step 3:** Wait for the next real live clip from the worker. Check that the captions use Archivo Black, the bubbles are readable and there is no end card.
- [ ] **Step 4:** Run both suites and the secret grep for the `.env` values, then commit any tuning.
