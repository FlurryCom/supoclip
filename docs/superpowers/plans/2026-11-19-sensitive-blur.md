# Sensitive Blur Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find nudity (NudeNet model) and heavy gore (Claude via the CLI) in each live clip once after render, and blur those regions per posting account at posting time. Switches on accounts and channels control it, and posting fails closed.

**Architecture:**
- The worker analyses the rendered clip, but only for live channels with `blur_sensitive` on. It samples frames with ffmpeg; the nudity boxes come from the NudeNet 320n ONNX model run with onnxruntime; the gore regions come from one Claude Haiku call on contact sheets. The result is stored in `live_clip_sensitive`, keyed by file path.
- The recorder decides per target (post, blur or refuse) and adds the blur to the same ffmpeg pass as the CTA.

**Tech Stack:** Python 3.12, onnxruntime, OpenCV (already in the worker), Claude CLI (`--input-format stream-json`, `--output-format stream-json`, `--json-schema`), ffmpeg (`crop`/`boxblur`/`overlay` with `enable`), psycopg, Next.js.

**Spec:** `docs/superpowers/specs/2026-11-19-sensitive-blur-design.md`

## Global Constraints

- The switch is `blur_sensitive boolean NOT NULL DEFAULT true` on both `live_channels` and `live_post_targets`. A clip is blurred for a target only when both are on, and analysed only when the channel's switch is on.
- The migration file is `20261119_0009_live_sensitive_blur.sql`. Another session's branch adds 0007 and 0008; don't touch those numbers.
- Nudity classes: `FEMALE_BREAST_EXPOSED`, `FEMALE_GENITALIA_EXPOSED`, `MALE_GENITALIA_EXPOSED`, `BUTTOCKS_EXPOSED`, `ANUS_EXPOSED`. Score ≥ 0.45, box padded 15%.
- Gore means heavy gore only: pools of blood, open wounds, dismemberment, executions up close. Ordinary bullet sprays don't count.
- Sampling: 2 fps for nudity; 1 fps contact sheets for gore, with 16 tiles (4x4), each 320 px wide and labelled with its second. The Claude model is `haiku`. The gore call is retried twice.
- Regions: `{"kind": "nudity"|"gore", "start": s, "end": s, "x": f, "y": f, "w": f, "h": f}`. Times are in clip seconds; positions are fractions of the frame. Gore regions use 4x4 grid cells.
- Blur: `boxblur=luma_radius=min(w\,h)/6:luma_power=3` on each region crop.
- Refuse when the union of blurred seconds is over 40% of the clip.
- Fail closed. When the blur is required and the clip is not checked (no row, `error` set, or a duration mismatch > 0.2 s), refuse with a reason. When the blur is required and the ffmpeg blur fails, also refuse (never post unblurred).
- Grandfathering: clips created before `live_feature_marks.at` for `sensitive_blur` (set by the migration) post as before.
- Never commit `.env` values. Tests:
  - backend: `scripts/backend-test.sh <paths>`;
  - recorder: `docker run --rm -v ./live:/app:z --entrypoint sh supoclip-live-recorder -c "pip install -q pytest >/dev/null; cd /app && python -m pytest -q tests"`.

## Review Focus

1. A target with the switch on and the Claude CLI down: the clip is refused with the reason and is never posted unblurred. Test `test_unchecked_clip_is_refused_when_blur_required` (Task 5).
2. A region whose box touches or leaves the frame edge (x+w > 1): it is clamped, and ffmpeg doesn't fail on the crop. Test `test_blur_args_clamp_regions_to_the_frame` (Task 5).
3. Overlapping regions (the same seconds counted twice) don't inflate the 40% rule. Test `test_blurred_share_uses_the_union_of_seconds` (Task 5).
4. Claude answering with tile or cell numbers out of range: they are ignored, without crashing. Test `test_gore_cells_out_of_range_are_dropped` (Task 2).
5. A clip without an audio stream still gets blur and CTA. Test `test_finish_blurs_a_silent_clip` (Task 5).

---

### Task 1: Settings, migration and switches

**Files:**
- Create: `backend/src/migrations/sql/20261119_0009_live_sensitive_blur.sql`
- Modify: `backend/src/api/routes/live.py`:
  - `_settings` and `_target_settings` accept a `blur_sensitive` bool;
  - the channel list SELECT and the targets list SELECT return it.
- Modify: `frontend/src/app/live/page.tsx` (a switch on the channel card) and `frontend/src/app/live/posting-section.tsx` (a switch on each target card)
- Modify: `frontend/messages/{pt,en,es,de,id,zh}/live.json` (keys `blurSensitive`, `blurSensitiveHint`)
- Test: `backend/tests/unit/test_live_routes.py`

**Interfaces:**
- Produces the following tables:
  - `live_channels.blur_sensitive`;
  - `live_post_targets.blur_sensitive`;
  - `live_clip_sensitive(file_path text PRIMARY KEY, duration double precision NOT NULL, regions jsonb NOT NULL DEFAULT '[]', error text, checked_at timestamptz NOT NULL DEFAULT now())`;
  - `live_feature_marks(name text PRIMARY KEY, at timestamptz NOT NULL DEFAULT now())`, with the row `('sensitive_blur', now()) ON CONFLICT DO NOTHING`.

- [ ] **Step 1: Write the failing tests:**
  - `test_settings_accept_blur_sensitive`: `_settings({"blur_sensitive": False}) == {"blur_sensitive": False}` and `_target_settings({"blur_sensitive": True}) == {"blur_sensitive": True}`;
  - `test_settings_reject_non_bool_blur_sensitive`: `"yes"` and `1` raise 422 in both.
- [ ] **Step 2: Run** `scripts/backend-test.sh tests/unit/test_live_routes.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **Backend:** the migration plus the route changes. Add the bool key to the existing bool loop in `_settings`, and the same check in `_target_settings`.
  - **Frontend:** a `Switch` with `Label`, the same pattern as the chat filter switch.
  - **Copy** (pt: `blurSensitive` "Borrar nudez e gore", `blurSensitiveHint` "Analisa os clipes e borra nudez e gore pesado antes de postar"), translated for en, es, de, id and zh.
- [ ] **Step 4: Run** the backend tests, then `DOCKER_BUILDKIT=0 COMPOSE_BAKE=false docker compose build frontend` (type check). Expected: PASS, build OK.
- [ ] **Step 5: Commit** `feat(live): blur-sensitive switches on channels and posting accounts`.

### Task 2: Gore check with Claude

**Files:**
- Modify: `backend/src/claude_cli.py` (add `run_cli_images`)
- Create: `backend/src/media/gore_check.py`
- Test: `backend/tests/unit/test_gore_check.py`

**Interfaces:**
- Produces:
  - `run_cli_images(prompt: str, images: list[bytes], schema: dict, model: str = "haiku", timeout: int = 180) -> dict`. It returns `structured_output` from the `"type":"result"` stream-json line and raises `RuntimeError` on `is_error` or a missing result. It reuses the env scrubbing, flags and cancellation handling of the existing `run_cli`.
  - `contact_sheets(frames: list[tuple[float, np.ndarray]]) -> list[tuple[bytes, list[float]]]`: JPEG bytes plus the second of each tile, 16 tiles per sheet.
  - `GORE_SCHEMA`: `{"type":"object","properties":{"gore":{"type":"array","items":{"type":"object","properties":{"sheet":{"type":"integer"},"tile":{"type":"integer"},"cells":{"type":"array","items":{"type":"integer"}}},"required":["sheet","tile","cells"]}}},"required":["gore"]}`.
  - `gore_regions(answer: dict, sheets_times: list[list[float]]) -> list[dict]`. Tiles count from 1, sheets count from 1 and cells run 1–16 (row-major on a 4x4 grid). Each tile's region lasts from its second to the second + 1.
  - `find_gore(frames, run=run_cli_images) -> list[dict]`, with 2 retries.

- [ ] **Step 1: Write the failing tests:**
  - `test_gore_regions_map_cells_to_frame_fractions`: sheet 1, tile 3 at second 2.0, cell 6 gives `{"kind":"gore","start":2.0,"end":3.0,"x":0.25,"y":0.25,"w":0.25,"h":0.25}`.
  - `test_gore_cells_out_of_range_are_dropped`: tile 99, cell 0 and cell 17 give no regions.
  - `test_contact_sheets_hold_16_tiles`: 20 frames give 2 sheets with 16 and 4 times.
  - `test_find_gore_retries_then_raises`: a fake `run` that fails 3 times raises; one that fails once and then answers `{"gore": []}` returns `[]`.
- [ ] **Step 2: Run** `scripts/backend-test.sh tests/unit/test_gore_check.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **Prompt** (English): look for heavy gore only (blood pools, open wounds, dismemberment, close-up executions); ignore ordinary gunfire blood sprays, game UI and webcam overlays; answer with the sheet, tile and 4x4 cells for each tile with heavy gore; answer an empty list if there is none.
  - **Sheets:** tiles drawn with OpenCV, the second written in the tile corner.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(kits): heavy gore check with Claude on contact sheets`.

### Task 3: Nudity check with the NudeNet model

**Files:**
- Modify: `backend/pyproject.toml` and `backend/uv.lock` (`uv add onnxruntime` in `backend/`)
- Create: `backend/assets/models/nudenet-320n.onnx` (from the nudenet 3.4.2 wheel, sha256 `c15d8273…911f0f`) and `backend/assets/models/NOTICE.md` (source, version, license: the wheel metadata says MIT, its LICENSE file is AGPL-3.0)
- Create: `backend/src/media/nudity_check.py`
- Test: `backend/tests/unit/test_nudity_check.py`

**Interfaces:**
- Produces:
  - `EXPOSED`, a frozenset of the five classes;
  - `NudityModel` (lazy singleton), with `detect(frame_bgr: np.ndarray) -> list[tuple[str, float, tuple[int,int,int,int]]]`. Preprocessing pads to a square, resizes to 320, scales to 1/255 and swaps to RGB. Postprocessing transposes the output; each row is `cx, cy, w, h` plus 18 class scores, with score ≥ 0.2 before NMS (`cv2.dnn.NMSBoxes`, 0.25 / 0.45), and boxes are scaled back to the frame.
  - `merge_regions(samples: list[tuple[float, list[tuple[float,float,float,float]]]], step: float, gap: float = 1.0) -> list[dict]`. The input is `(second, boxes as fractions)`, already filtered by class and score and padded. Overlapping boxes on consecutive samples join into one region whose box is their union, and the end is the last second plus `step`.
  - `find_nudity(frames: list[tuple[float, np.ndarray]], step: float = 0.5) -> list[dict]`.

- [ ] **Step 1: Write the failing tests:**
  - `test_merge_regions_joins_consecutive_overlapping_boxes`: boxes at 1.0, 1.5 and 2.0 that overlap give one region from 1.0 to 2.5 with the union box.
  - `test_merge_regions_keeps_separate_places_apart`: two boxes that never overlap give two regions.
  - `test_merge_regions_pads_and_clamps`: a box at the edge stays inside [0, 1].
  - `test_model_loads_and_finds_nothing_on_a_blank_frame`: a 640x360 black frame gives `[]`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Add the dependency (`uv add onnxruntime`), copy the model and write the notice.
  - Rebuild the backend image so `scripts/backend-test.sh` has onnxruntime: `DOCKER_BUILDKIT=0 COMPOSE_BAKE=false docker compose build backend`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(kits): local nudity check with the NudeNet model`.

### Task 4: The worker analyses live clips after render

**Files:**
- Create: `backend/src/media/sensitive_check.py`
- Modify: `backend/src/live_scoring.py` (select `c.blur_sensitive`; set `segment["sensitive_check"] = True` on kept segments when it is on)
- Modify: `backend/src/services/video_service.py` (after the edit kit pass: when `segment.get("sensitive_check")`, run `analyze_clip` in a thread and upsert the row)
- Test: `backend/tests/unit/test_sensitive_check.py`

**Interfaces:**
- Produces:
  - `sample_frames(path, fps) -> list[tuple[float, np.ndarray]]`, decoded with ffmpeg to raw BGR at 640 px wide;
  - `analyze_clip(path, find_nudity=..., find_gore=...) -> dict`, which returns `{"duration", "regions", "error"}`. Nudity regions are kept even when gore fails, in which case `error = "gore check failed: …"`.
  - `save_analysis(file_path, result)` (async; `INSERT ... ON CONFLICT (file_path) DO UPDATE`).

- [ ] **Step 1: Write the failing tests:**
  - `test_analyze_clip_merges_both_checks`: with fake finders, the result has nudity and gore regions and `error None`.
  - `test_analyze_clip_keeps_nudity_when_gore_fails`: fake gore raises; the nudity regions stay and `error` starts with "gore check failed".
  - `test_sample_frames_reads_two_per_second`: a 3 s synthetic clip at 2 fps gives 6 frames at 0.0, 0.5, … 2.5.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Wire the gate and `video_service`. Log `Sensitive check <file>: N nudity, M gore, error=…`.
  - Any exception in the whole analysis is saved as `error`; it never fails the render.
- [ ] **Step 4: Run** the tests and the kit and live test files. Expected: PASS.
- [ ] **Step 5: Commit** `feat(kits): analyse live clips for nudity and gore after render`.

### Task 5: The recorder blurs per account and fails closed

**Files:**
- Create: `live/supoclip_live/sensitive.py`
- Modify: `live/supoclip_live/cta.py`. `cta_args` takes `regions: list[dict] | None` and puts the blur chain before the CTA overlay. Add `finish_clip(clip, kit, provider, regions, must_blur) -> Path`, which raises `BlurFailed` when `must_blur` and anything fails, and otherwise falls back like `with_cta`. A video without a CTA but with regions still gets the blur.
- Modify: `live/supoclip_live/service.py` (`post_clips` selects `t.blur_sensitive AS target_blur, c.blur_sensitive AS channel_blur, s.duration AS checked_duration, s.regions, s.error AS check_error, g.created_at AS clip_created`, LEFT JOINs `live_clip_sensitive s ON s.file_path = g.file_path`, and reads the mark once per cycle; then it decides, and either refuses or calls `finish_clip`)
- Test: `live/tests/test_sensitive.py`

**Interfaces:**
- Produces:
  - `blurred_share(regions, duration) -> float` (the union of `[start, end]` divided by the duration);
  - `decide(row: dict, since: datetime, actual_duration: float) -> tuple[str, str | None, list]`, which returns `("post", None, [])`, `("blur", None, regions)` or `("refuse", reason, [])`. The reasons are in Portuguese, for the Live page: "clipe não analisado", "análise falhou", "clipe alterado depois da análise" and "mais de 40% borrado";
  - `blur_chain(regions, width, height, label_in, label_out) -> list[str]`.

- [ ] **Step 1: Write the failing tests:**
  - `test_both_switches_needed_to_blur`: if either switch is off, the result is `("post", None, [])`.
  - `test_unchecked_clip_is_refused_when_blur_required`: with no row (`checked_duration` None) and a clip created after `since`, the result is refuse "clipe não analisado"; with `check_error` set, refuse "análise falhou".
  - `test_old_clips_are_grandfathered`: with no row and a clip created before `since`, the result is post.
  - `test_duration_mismatch_refuses`: `checked_duration` 60.0 against an actual 61.0 gives refuse "clipe alterado depois da análise".
  - `test_blurred_share_uses_the_union_of_seconds`: regions [0,10] and [5,15] on a 50 s clip give 0.3.
  - `test_over_40_percent_is_refused`.
  - `test_blur_args_clamp_regions_to_the_frame`: x=0.9, w=0.3 gives a crop width that ends at the frame edge, an even crop size, and `enable='between(t,1,2)'`.
  - `test_finish_blurs_a_silent_clip` (ffmpeg smoke): a 4 s silent 720x1280 clip with one region is blurred. The pixel variance inside the region at t=1.5 falls compared with the input, and the output has audio.
  - `test_finish_raises_when_blur_is_required_and_fails`: a garbage file with `must_blur=True` raises `BlurFailed`.
- [ ] **Step 2: Run** the recorder tests. Expected: FAIL.
- [ ] **Step 3: Implement.** In `post_clips`:
  - on refuse, record the post as "refused" with the reason;
  - on `BlurFailed`, record "refused" with "falha ao borrar";
  - otherwise upload the finished file.
- [ ] **Step 4: Run** the recorder tests. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(live): blur nudity and gore per account before posting, failing closed`.

### Task 6: Deploy and check

- [ ] **Step 1:**
  - Rebuild the `worker` and `ai-worker` images (they share the backend Dockerfile, and onnxruntime is needed).
  - Wait for `processing = 0`.
  - Restart `backend` (migration 0009), then `worker` and `frontend`, then rebuild and restart `live-recorder`.
- [ ] **Step 2:** Check the switches on the Live page (channel cards and account cards) and that the defaults are on.
- [ ] **Step 3:** Run `analyze_clip` inside the worker on the newest live clip.
  - Check the timing and the result: probably no regions on normal gameplay.
  - Then run `finish_clip` in the recorder with one injected fake region, and look at a frame to confirm the blur.
- [ ] **Step 4:** Wait for the next real live clip and confirm its `live_clip_sensitive` row exists. Run both suites and check for secrets.
