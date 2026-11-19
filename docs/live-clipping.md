# Live clipping (fork feature)

Follow Twitch channels. While a channel is live, SupoClip records it and sends
stretches of the broadcast through the normal clipping pipeline, so live clips
get the same selection, framing, captions and editor as VOD clips. Clips land in
the Library about 15 minutes after the moment.

## Turn it on

1. In `.env`, add the `live` compose profile and your time zone for clip titles:
   ```env
   COMPOSE_PROFILES=live
   LIVE_TIMEZONE=America/Sao_Paulo
   ```
2. `docker compose up -d --build` (starts the `live-recorder` service).
3. Enable the `live_clipping` flag in `/admin` (admins see the feature already).
4. Open **Live** in the sidebar and follow a channel by name or `twitch.tv` link.

## How it works

- `live/` is a separate service. It checks each followed channel with
  streamlink every minute, records live ones (`streamlink | ffmpeg`, 60 s
  chunks named by wall clock) and reads chat anonymously.
- Every 10 minutes of new broadcast it decides on a **window**: the new part
  plus 10 minutes of context before it (20 minutes). While a channel already has
  a window in the pipeline, content accumulates and the next window covers it,
  up to 40 minutes.
- **Chat filter** (on by default, per channel): a window is processed only if
  chat spiked in its new part, meaning a 10-second stretch with at least three
  times the usual number of distinct chatters. Bots and `!commands` don't count. A
  spike near the end waits for two more minutes of aftermath. Turn it off for
  talk streams with a quiet chat; every window is then processed.
- Windows go through `POST /upload` and `POST /tasks/` with the frontend's
  signed headers, so preflight, minute charging and admission apply unchanged.
- Windows use the **long** clip preset (61-120 s, so clips qualify for TikTok
  monetization); `LIVE_CLIP_LENGTH` changes it.
- Chat is stored as distinct chatters per 10 seconds (`live_chat_buckets`).
  Before a window is sent, the points where chat exploded are passed to clip
  selection as creator instructions (on the window file's clock), so the AI
  cuts around them. This uses the instructions path, so the `workflows`
  feature flag must be on.
- Each clip gets a **chat heat** (0-100): its chat peak, from its start to 15 s
  after its end, ranked against every stretch of the same length in that
  broadcast. Its **final score** is
  `(100 - chat_weight)% × AI score + chat_weight% × chat heat`
  (`chat_weight` per channel, default 60%); without chat data the AI score stands.
- When a task finishes, clips shorter than `LIVE_MIN_CLIP_SECONDS` (61), clips
  whose final score is below the channel's **minimum score**, and clips
  that overlap more than half of an already-kept clip (on the broadcast clock)
  are deleted. A window can end with zero clips.
- Chunks are deleted once no future window can use them; a 1080p60 stream takes
  about 3-4 GB per hour, and at most about 50 minutes is kept per live channel.

State lives in `live_channels`, `live_recordings`, `live_windows` and
`live_clips` (`backend/src/migrations/sql/20261119_0001_live_clipping.sql`).

## Limits

- Twitch only for now; Kick and YouTube can follow the same path.
- Each window counts as one upload. `MEDIA_UPLOAD_DAILY_LIMIT` (default 20 per
  user per day) caps how many windows a day go through; raise it for heavy use.
  `daily_window_cap` (default 12) is the per-channel limit.
- Spikes are kept in memory. A recorder restart mid-stream forgets the spikes
  of the part not yet sent (at most one window).
- Local Whisper `medium` on CPU is far slower than real time: on a 16-core
  machine a 3-minute window took 16.5 minutes to transcribe (part of it sharing
  the CPU with another transcription). It cannot keep up with a live channel.
  Run Whisper on an NVIDIA GPU (`docker-compose.gpu.yml`, where `small`
  transcribed the same 3 minutes in about 12 seconds), use
  `TRANSCRIPTION_PROVIDER=assemblyai`, and keep the chat filter on.
- Recording is capped: `LIVE_MAX_CHANNELS_PER_USER` (default 5) channels per
  account and `LIVE_MAX_RECORDINGS` (default 3) broadcasts at once, shared
  fairly between the accounts that follow channels.
- The SupoClip license forbids commercial use without permission from Shiori
  LLC, and clipping someone else's stream is subject to Twitch's terms and the
  streamer's rights.

## Tests

```bash
cd live && uv run --no-project --with pytest --with httpx --with "psycopg[binary]" python -m pytest -q tests
```

## Posting

Kept live clips can be posted through [Postiz](https://postiz.com) to TikTok,
YouTube Shorts and Instagram.

1. In `.env`, set `POSTIZ_URL` (your Postiz base URL), `POSTIZ_API_KEY`
   (Postiz **Settings → Public API**) and `POSTIZ_ALLOWED_USER_IDS` (the SupoClip
   user ids allowed to use it; the key serves the whole server), then restart
   the backend and recorder.
2. On the Live page, **Auto-posting** lists the accounts connected in Postiz.
   Only accounts you **authorize** there ever receive posts.

Safety rules, enforced by the recorder before every post:

- A post goes only to the authorized integration id; there is no default or
  fallback account.
- Authorizing stores the provider and name Postiz reports for that id. If the
  id disappears, changes network, is renamed or is disabled, the post is
  refused and shown as refused; posting there resumes only after you authorize
  it again.
- Each clip is posted at most once per account, only clips made after the
  account was authorized are posted, and an account gets at most `daily_cap`
  (10) posts per 24 h.

Per account: on/off, **mode** (`draft` lands in Postiz for approval, the
default; `now` publishes immediately) and the **minimum final score** to post
(default 80). The caption is the clip's hook title, a credit line
(`twitch.tv/<channel>`) and hashtags.
