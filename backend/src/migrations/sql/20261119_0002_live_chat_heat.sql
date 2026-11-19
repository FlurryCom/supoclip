-- Live clipping: how hard chat reacted, per clip (fork feature, docs/live-clipping.md).
-- The recorder stores distinct chatters per 10-second bucket of each broadcast;
-- a clip's chat heat (0-100) compares its peak with the rest of that broadcast,
-- and its final score mixes the AI's virality score with chat heat by the
-- channel's chat_weight.
ALTER TABLE live_channels
    ADD COLUMN IF NOT EXISTS chat_weight INTEGER NOT NULL DEFAULT 60 CHECK (chat_weight BETWEEN 0 AND 100);

CREATE TABLE IF NOT EXISTS live_chat_buckets (
    recording_id VARCHAR(36) NOT NULL REFERENCES live_recordings(id) ON DELETE CASCADE,
    at TIMESTAMPTZ NOT NULL,
    chatters INTEGER NOT NULL,
    spike BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (recording_id, at)
);

ALTER TABLE live_clips ADD COLUMN IF NOT EXISTS ai_score INTEGER;
ALTER TABLE live_clips ADD COLUMN IF NOT EXISTS chat_heat INTEGER;
ALTER TABLE live_clips ADD COLUMN IF NOT EXISTS final_score INTEGER;
