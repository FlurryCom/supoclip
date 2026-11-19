# Cat Clips Edit Kit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live clips get a switchable "edit kit": Cat Clips captions, real chat popping on screen at the chat peak, sound effects and a branded end card. Each live channel picks its kit on the Live page.

**Architecture:** A kit registry in the backend (`edit_kits.py`) names a caption template plus assets. The live recorder stores a filtered sample of chat texts per 10 s bucket. The pre-render score gate (`live_scoring.py`), which already reads the window's chat, attaches the channel's kit, the peak time and the peak's messages to each kept segment. The normal render uses the kit's caption template. A new post-pass (`apply_edit_kit`) burns chat bubbles, mixes the sound effects and overlays the end card in one extra ffmpeg run, and falls back to the plain clip if it fails.

**Tech Stack:** Python 3.12, FastAPI, SQL migrations (auto-run on backend start), ffmpeg/libass, Pillow, Next.js + next-intl (6 locales), psycopg (recorder).

**Spec:** `docs/superpowers/specs/2026-11-19-cat-clips-kit-design.md`

## Global Constraints

- Kits: `none` (SupoClip's normal look, current behavior) and `catclips`. Column `live_channels.edit_kit` defaults to `none`.
- Brand: yellow `#FEC803`, black `#111111`, cream `#F3E9D2`. Assets under `backend/assets/brand/catclips/`.
- Chat on screen: text only, never the author. Drop links, @mentions, `!commands`, lines over 60 characters and blocklisted words. At most 20 stored per 10 s bucket, at most 8 shown per clip.
- Clip length never changes (61 s TikTok minimum): the end card sits inside the last 1.5 s.
- Sound effects are CC0 from MotionMoon: `cartoon-whoosh.wav` at 0 s and `sfx-golpe-abertura.wav` at the peak. Copy each one's license line.
- The kit never costs a clip: any failure in the post-pass keeps the clip rendered without it.
- No GPU use in the kit (Whisper keeps the GPU).
- Backend tests: `scripts/backend-test.sh <test paths>` (runs in a throwaway backend container). Recorder tests: `docker run --rm -v ./live:/app:z --entrypoint sh supoclip-live-recorder -c "pip install -q pytest >/dev/null; cd /app && python -m pytest -q tests"`.
- Never commit the Postiz URL or key: `git grep -c 'ed4ef9aa\|postiz-production'` must print nothing.

## Review Focus

1. A segment whose window has no chat messages (old recordings, YouTube lag, `none` kit): the clip still renders, with no overlay, and doesn't crash. Test in Task 3 (`test_gate_without_messages_attaches_kit_without_chat`) and Task 4 (`test_kit_args_without_chat_still_mix_whoosh_and_card`).
2. A peak near the clip edges (first seconds or within the end card): bubbles are clamped so they finish before the card. Test in Task 4 (`test_chat_events_clamped_before_end_card`).
3. Chat text with ASS control characters (`{`, `}`, `\N`, backslashes): it must show as literal text and never inject override tags. Test in Task 4 (`test_chat_text_is_escaped`).
4. Toxic or doxxing chat (slur, link, @handle, a long copy-paste): never reaches the screen. Test in Task 2 (`test_keep_message_filters`).
5. An unknown kit name in the database or a request: the request is rejected with 422, and the render treats an unknown stored kit as `none`. Tests in Task 1 (`test_unknown_kit_is_none`) and Task 5 (`test_patch_rejects_unknown_kit`).

---

### Task 1: Kit registry, Cat Clips caption template and assets

**Files:**
- Create: `backend/src/edit_kits.py`
- Modify: `backend/src/caption_templates.py` (add `"catclips"`)
- Create: `backend/assets/sfx/cartoon-whoosh.wav`, `backend/assets/sfx/sfx-golpe-abertura.wav`, `backend/assets/sfx/LICENSES.md`
- Create: `backend/fonts/GochiHand-Regular.ttf` plus the OFL license text, and add a line to `backend/fonts/SOURCES.md`
- Create: `backend/assets/brand/catclips/make_end_card.py`, `backend/assets/brand/catclips/end_card.png` (1080x1920 RGBA)
- Modify: `docker-compose.yml` (mount `./backend/assets:/app/assets` on backend and worker, next to the `./backend/fonts` mount), `backend/Dockerfile` (`COPY assets/ ./assets/`)
- Test: `backend/tests/unit/test_edit_kits.py`

**Interfaces:**
- Produces: `KITS: dict[str, dict]`, `get_kit(name: str | None) -> dict` (unknown or None returns `KITS["none"]`), `kit_choices() -> list[dict]` (`[{"id": "none", "name": "SupoClip"}, {"id": "catclips", "name": "Cat Clips"}]`). A kit dict has the keys `id`, `name`, `caption_template` (`None` for `none`), `chat_overlay: bool`, `sfx: list[{"path": Path, "at": "start" | "peak", "volume": float}]`, `end_card: Path | None`, `end_card_seconds: float` (1.5), `colors: {"yellow", "black", "cream"}`. `ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"`.

- [ ] **Step 1: Write the failing tests** in `test_edit_kits.py`:
  - `test_catclips_kit_points_at_existing_assets`: every `sfx[i]["path"]` and `end_card` of `get_kit("catclips")` exists, and `caption_template == "catclips"`.
  - `test_unknown_kit_is_none`: `get_kit("nope") is KITS["none"]` and `get_kit(None) is KITS["none"]`. `KITS["none"]` has `chat_overlay False`, `sfx []` and `end_card None`.
  - `test_catclips_caption_template`: `get_template("catclips")` has `uppercase True`, `emoji False`, `word_box True`, `word_box_color "#FEC803"`, `highlight_color "#111111"`, `font_color "#F3E9D2"`, `stroke_color "#111111"` and `font_family "GochiHand"`.
- [ ] **Step 2: Run** `scripts/backend-test.sh tests/unit/test_edit_kits.py`. Expected: FAIL (`No module named src.edit_kits`).
- [ ] **Step 3: Add the assets.**
  - Download `GochiHand-Regular.ttf` and `OFL.txt` from `https://github.com/google/fonts/raw/main/ofl/gochihand/` into `backend/fonts/` (the owner approved the download). Add a line to `SOURCES.md`.
  - Copy the two sounds from `~/projects/Brainsidian/MotionMoon/biblioteca/sons/`. Write `LICENSES.md` with each file's `licenca` and `autor`/`pagina` from that folder's `sons.json`.
  - `make_end_card.py` (Pillow) draws a 1080x1920 card: yellow background, `avatar.jpg` scaled to a 760 px circle-cropped square centred at y=760, "CAT CLIPS" in GochiHand 150 px black with a cream offset shadow (+6, +6) at y=1260, and "Siga pra mais" in GochiHand 80 px black at y=1420. Run it once and commit the PNG.
- [ ] **Step 4: Implement** `edit_kits.py` and the `catclips` template entry. Use the same fields as `"hormozi"` with the values from the test, `max_words_per_line 3`, `position_y 0.74`, `word_pop True`, `shadow True`, `animation "karaoke"`. Volumes: whoosh 0.5, hit 0.7 (tuned by ear in Task 6). Font family name: check what `ass_font_name()` and `ass_fonts_dir()` expect for a file in `backend/fonts` and match it.
- [ ] **Step 5: Run** the tests. Expected: PASS. Also run `scripts/backend-test.sh tests/unit/test_caption_render_roundtrip.py`. Expected: still PASS.
- [ ] **Step 6: Commit** `feat(kits): edit kit registry and Cat Clips template and assets`.

### Task 2: Recorder keeps a filtered sample of chat texts

**Files:**
- Modify: `live/supoclip_live/chat.py` (`keep_message`, the `SpikeDetector` sample, `drain()` tuples)
- Modify: `live/supoclip_live/service.py` (`on_bucket` signature and `store_bucket` insert)
- Create: `backend/src/migrations/sql/20261119_0006_live_edit_kits.sql` (`ALTER TABLE live_chat_buckets ADD COLUMN IF NOT EXISTS messages jsonb;` and `ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS edit_kit text NOT NULL DEFAULT 'none';`)
- Test: `live/tests/test_chat.py`, `live/tests/test_service.py`

**Interfaces:**
- Produces:
  - `keep_message(text: str) -> bool`.
  - `SpikeDetector.drain() -> list[tuple[datetime, int, bool, list[str]]]`, where the 4th item holds at most 20 kept texts, first come first kept, one per user.
  - `on_bucket(at, chatters, spike, messages)`.
  - DB column `live_chat_buckets.messages jsonb` (a JSON array of strings, NULL for old rows).

- [ ] **Step 1: Write the failing tests** in `test_chat.py`:
  - `test_keep_message_filters`:
    - True for `"KEKW que isso"`;
    - False for `"http://x.com"`, `"www.site.com"`, `"@fulano olha"`, `"!points"`, `"a" * 61`, `""` and a blocklisted word.
    - The blocklist is a module constant `BLOCKLIST`. Seed it with common PT and EN slurs, matched as whole words, case-insensitive.
  - `test_bucket_keeps_a_sample_of_messages`: after `warm(d)` and 25 distinct users in bucket 20, `d.tick(...)` closes it. The last `drain()` item has `len(messages) == 20`, and every item is a str.
  - `test_spammer_lines_count_once_in_sample`: one user sending 10 lines in a bucket gives at most 1 message in the sample.
  - Update the existing tests that unpack `drain()` tuples (`grep -rn drain live/tests`).
- [ ] **Step 2: Run** the recorder tests (the command is in Global Constraints). Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
  - `add()` appends to the bucket's sample when `keep_message(message)` is true, the user is not a bot, the user has no line in this bucket's sample yet, and the sample has fewer than 20.
  - `_roll` puts the sample into the closed tuple and resets it.
  - `service.on_bucket` passes `messages`.
  - `store_bucket` inserts `json.dumps(messages)` into the new column.
  - Write the migration file.
- [ ] **Step 4: Run** the recorder tests. Expected: all PASS (43 existing plus the new ones).
- [ ] **Step 5: Commit** `feat(live): keep a filtered sample of chat texts per bucket`.

### Task 3: The score gate attaches the kit, the peak and its messages

**Files:**
- Modify: `backend/src/live_scoring.py`
- Test: `backend/tests/unit/test_live_scoring.py`

**Interfaces:**
- Consumes: `get_kit` (Task 1) and the `messages` and `edit_kit` columns (Task 2).
- Produces:
  - `peak_overlay(segment: dict, chunks, buckets_with_messages: list[tuple[datetime, int, list[str]]]) -> dict | None`. It returns `{"peak_s": float, "chat": list[str]}` with clip-relative seconds, or None when the segment has no bucket with messages.
  - After `gate_live_segments`, each kept segment of a live window carries `segment["edit_kit"] = {"id": str, "peak_s": float | None, "chat": list[str]}`. The key is absent for non-live tasks.
  - Constant `CHAT_LAG_S = 8` (chat reacts after the moment).

- [ ] **Step 1: Write the failing tests:**
  - `test_peak_overlay_picks_hottest_bucket_and_shifts_for_lag`: a segment of 04:10–05:10 on `CHUNKS`, with the BUCKETS from this file where bucket 30 (300 s) has messages `["a", "b"]`. The result is `peak_s == 300 - 250 - 8 == 42` and `chat == ["a", "b"]`.
  - `test_peak_overlay_takes_neighbours_up_to_eight`: the peak bucket has 6 messages and each neighbour has 4. `len(chat) == 8`, with the peak's messages first.
  - `test_gate_without_messages_attaches_kit_without_chat`: `peak_overlay(...)` returns None when no bucket in the span has messages. The `edit_kit` built for that segment has `peak_s None` and `chat []`. Test it through a small pure helper `segment_kit(kit_id, overlay) -> dict`.
- [ ] **Step 2: Run** `scripts/backend-test.sh tests/unit/test_live_scoring.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Add `c.edit_kit` to the gate's SELECT, and select `messages` with the buckets (NULL becomes `[]`).
  - Score with the `(at, chatters)` pairs as before.
  - For each kept segment, set `edit_kit` only when `get_kit(row.edit_kit)["id"] != "none"`.
  - The peak is the highest-chatters bucket within `[start, end + LAG]`.
  - `peak_s = (bucket_at - segment_start_wall).total_seconds() - CHAT_LAG_S`, clamped to `>= 0.5`.
  - Code comment: `shortcut: clip time ignores silence cuts inside keep_ranges, fine for 1-2 s drift; map through keep_ranges if bubbles land visibly late`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(live): attach edit kit and chat peak to kept live segments`.

### Task 4: `apply_edit_kit` post-pass (chat bubbles, sound effects, end card)

**Files:**
- Create: `backend/src/media/edit_kit_render.py`
- Test: `backend/tests/unit/test_edit_kit_render.py`

**Interfaces:**
- Consumes: `get_kit` (Task 1) and the `segment["edit_kit"]` shape (Task 3).
- Produces:
  - `chat_events(chat: list[str], peak_s: float | None, duration: float, width: int, height: int, colors: dict, end_card_seconds: float) -> list[str]`: ASS `Dialogue:` lines.
  - `chat_ass(...) -> str`: a full ASS document (script info with `PlayResX/PlayResY = width/height`, one `Bubble` style, plus the events).
  - `kit_ffmpeg_args(clip: Path, out: Path, kit: dict, ass_path: Path | None, peak_s: float | None, duration: float, fonts_dir: Path) -> list[str]`.
  - `apply_edit_kit(clip_path: Path, edit_kit: dict) -> bool`: runs the pass and replaces `clip_path` on success. On any failure it returns False and leaves the file untouched.

- [ ] **Step 1: Write the failing tests:**
  - `test_chat_events_pop_in_order`: 3 messages, `peak_s=10`, `duration=70`. There are 3 events with start times `10.0, 10.4, 10.8`, each lasting 2.5 s.
  - `test_chat_events_clamped_before_end_card`: `peak_s=69`, `duration=70` and 8 messages. Every event ends `<= 70 - 1.5`. If they don't fit, the first message starts earlier rather than any being cut.
  - `test_chat_text_is_escaped`: the message `"{\\b1}oi\\N"` shows up escaped, using the existing `escape_ass_text` from `media/captions.py`, with no raw `{` in the event text.
  - `test_kit_args_without_chat_still_mix_whoosh_and_card`: with `ass_path=None` and `peak_s=None`, the args contain the end card input and the whoosh input, contain no `subtitles=` filter and no hit sound, and the `-filter_complex` has an `amix` and an `overlay` enabled from `duration - 1.5`.
  - `test_apply_edit_kit_smoke`: ffmpeg is in the image. Make a 4 s 1080x1920 test clip with `-f lavfi -i color=c=blue:s=1080x1920:d=4 -f lavfi -i sine=d=4`. Then `apply_edit_kit(path, {"id": "catclips", "peak_s": 1.0, "chat": ["oi", "KKKK"]})` returns True, and ffprobe shows the same duration ±0.1 s, 1080x1920, with an audio stream.
  - `test_apply_edit_kit_failure_keeps_clip`: on a path holding non-video bytes it returns False and the bytes are unchanged.
- [ ] **Step 2: Run** `scripts/backend-test.sh tests/unit/test_edit_kit_render.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **Bubbles:** ASS `BorderStyle=3` (opaque box) with a black box, cream text and a yellow outline. Font GochiHand at `height * 0.03`. Left-aligned (`\an7`), `x = width * 0.06`. They stack upward from `y = height * 0.55`, one line height apart, so they stay clear of the captions at 0.74. Each pops with `\fscx80\fscy80\t(0,120,\fscx100\fscy100)`.
  - **ffmpeg graph:**
    - video: `[0:v]subtitles=...[s]` when there is chat, then `[1:v]scale=W:H,format=rgba,fade=in:st=0:d=0.3:alpha=1,setpts=PTS+(D-1.5)/TB[card]` and `[s][card]overlay=enable='gte(t,D-1.5)'`;
    - audio: each sound goes through `adelay` (start, or `peak_s` for the hit) and `volume`, then `[0:a][sfx...]amix=inputs=N:normalize=0:duration=first`;
    - encoding: `-c:v libx264 -preset fast -crf 20 -c:a aac -b:a 192k -movflags +faststart`, the same as `burn_ass_subtitles_ffmpeg`.
  - Write to a temp file next to the clip and `os.replace` only when ffmpeg succeeds and ffprobe sees a video stream.
  - Run ffmpeg with the existing `run_ffmpeg_command`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(kits): post-pass that burns chat, mixes sfx and adds the end card`.

### Task 5: Wire the kit into rendering, the API and the Live page

**Files:**
- Modify: `backend/src/services/video_service.py` (around the `create_optimized_clip` call, ~line 1032)
- Modify: `backend/src/api/routes/live.py` (`_settings`, the channel SELECT and INSERT, new `GET /live/edit-kits`)
- Modify: `frontend/src/app/live/page.tsx` (a `Select` on the channel card, next to the sliders; `Settings` type gets `edit_kit`)
- Modify: `frontend/messages/{pt,en,es,de,id,zh}/live.json` (keys `editKit`, `editKitHint`)
- Test: `backend/tests/unit/test_live_routes.py`

**Interfaces:**
- Consumes: `get_kit` and `kit_choices` (Task 1), `segment["edit_kit"]` (Task 3) and `apply_edit_kit` (Task 4).
- Produces:
  - `GET /live/edit-kits` returns `kit_choices()`.
  - `PATCH /live/channels/{id}` accepts `{"edit_kit": "<id>"}`.
  - The channel list includes `edit_kit`.

- [ ] **Step 1: Write the failing tests** in `test_live_routes.py`, following that file's style for `_settings`:
  - `test_patch_accepts_known_kit`: `_settings({"edit_kit": "catclips"}) == {"edit_kit": "catclips"}`.
  - `test_patch_rejects_unknown_kit`: `_settings({"edit_kit": "nope"})` raises an HTTPException with status 422.
- [ ] **Step 2: Run** `scripts/backend-test.sh tests/unit/test_live_routes.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **`_settings`:** validate `edit_kit` against `KITS`.
  - **SELECT:** add `c.edit_kit` to the channel list SELECT.
  - **`video_service`:**
    - compute `kit = get_kit((segment.get("edit_kit") or {}).get("id"))`;
    - pass `kit["caption_template"] or caption_template` to `create_optimized_clip`;
    - after `success`, and before the resolution cap, when `segment.get("edit_kit")` is set: `await run_in_thread(apply_edit_kit, clip_path, segment["edit_kit"])`, logging a warning when it returns False.
  - **Frontend:**
    - fetch `/api/live/edit-kits` once through the existing proxy, and render a `Select` that calls `onChange({ edit_kit })`;
    - copy in pt: `editKit: "Kit de edição"`, `editKitHint: "Visual aplicado nos próximos clipes deste canal"`;
    - translate the same copy into en, es, de, id and zh.
- [ ] **Step 4: Run** the backend tests (`test_live_routes.py` and `test_live_scoring.py`) and `cd frontend && npx tsc --noEmit`. Expected: PASS and no type errors.
- [ ] **Step 5: Commit** `feat(kits): pick an edit kit per live channel and apply it on render`.

### Task 6: Deploy and check on a real clip

**Files:** none new (this task tunes the volumes and positions in `edit_kits.py` and `edit_kit_render.py` if needed)

- [ ] **Step 1:** Rebuild the images and restart the services.
  ```bash
  DOCKER_BUILDKIT=0 COMPOSE_BAKE=false docker compose build frontend live-recorder
  ```
  Then `docker compose up -d backend worker frontend live-recorder`, waiting for no render in progress first (`select count(*) from tasks where status='processing'` returns 0), because a worker restart orphans running tasks.
- [ ] **Step 2:** Check that the migration ran: `\d live_channels` shows `edit_kit` and `\d live_chat_buckets` shows `messages`.
- [ ] **Step 3:** Switch the channels to the kit: `UPDATE live_channels SET edit_kit='catclips'`.
- [ ] **Step 4:** Render one clip from the newest done window through the normal pipeline, or wait for the next live window. Pull a frame at the peak and one in the last second with ffmpeg, and look at both. Then listen: whoosh at 0 s, hit at the peak, and the original audio is still clear.
- [ ] **Step 5:** Check the Live page: the dropdown shows "Kit de edição" with SupoClip and Cat Clips, and switching it persists after a reload.
- [ ] **Step 6:** Run the full recorder and live backend test suites, run the secret grep, then commit any tuning: `chore(kits): tune Cat Clips levels and positions`.
