-- Live clipping (fork feature): channels a user follows, each live broadcast
-- recorded by the live-recorder service, the windows of it sent to the normal
-- task pipeline, and where each kept clip sits on the broadcast's clock.
-- Gated by the live_clipping feature flag; the recorder is the compose
-- profile "live". See docs/live-clipping.md.
CREATE TABLE IF NOT EXISTS live_channels (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform VARCHAR(16) NOT NULL CHECK (platform IN ('twitch')),
    channel VARCHAR(64) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    -- Only windows with a chat spike are processed. Off: every window is.
    chat_filter BOOLEAN NOT NULL DEFAULT true,
    -- Clips below this virality score (0-100) are deleted after rendering.
    min_score INTEGER NOT NULL DEFAULT 60 CHECK (min_score BETWEEN 0 AND 100),
    -- Most windows sent to the pipeline per channel in 24 hours.
    daily_window_cap INTEGER NOT NULL DEFAULT 12 CHECK (daily_window_cap BETWEEN 1 AND 200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, platform, channel)
);
CREATE INDEX IF NOT EXISTS idx_live_channels_enabled ON live_channels (enabled);

CREATE TABLE IF NOT EXISTS live_recordings (
    id VARCHAR(36) PRIMARY KEY,
    channel_id VARCHAR(36) NOT NULL REFERENCES live_channels(id) ON DELETE CASCADE,
    title TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    -- Broadcast time up to which content was sent or skipped.
    pending_from TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_live_recordings_open ON live_recordings (channel_id) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS live_windows (
    id VARCHAR(36) PRIMARY KEY,
    recording_id VARCHAR(36) NOT NULL REFERENCES live_recordings(id) ON DELETE CASCADE,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    -- submitted, done, skipped, error
    status VARCHAR(16) NOT NULL,
    reason TEXT,
    task_id VARCHAR(36) REFERENCES tasks(id) ON DELETE SET NULL,
    -- [{"start": iso8601 wall clock, "duration": seconds}] in file order
    chunks JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_live_windows_recording ON live_windows (recording_id, created_at);
CREATE INDEX IF NOT EXISTS idx_live_windows_submitted ON live_windows (status) WHERE status = 'submitted';

CREATE TABLE IF NOT EXISTS live_clips (
    clip_id VARCHAR(36) PRIMARY KEY REFERENCES generated_clips(id) ON DELETE CASCADE,
    window_id VARCHAR(36) NOT NULL REFERENCES live_windows(id) ON DELETE CASCADE,
    recording_id VARCHAR(36) NOT NULL REFERENCES live_recordings(id) ON DELETE CASCADE,
    abs_start TIMESTAMPTZ NOT NULL,
    abs_end TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_live_clips_recording ON live_clips (recording_id, abs_start);

INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'live_clipping',
    'Follow Twitch channels and clip their live broadcasts. Needs the live-recorder service (compose profile "live").',
    false,
    false,
    true
)
ON CONFLICT (key) DO NOTHING;
