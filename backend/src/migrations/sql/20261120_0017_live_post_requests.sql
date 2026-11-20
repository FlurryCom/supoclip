-- Clips the owner chose to publish by hand from the Library (any score); the recorder posts them.
CREATE TABLE IF NOT EXISTS live_post_requests (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    clip_id VARCHAR(36) NOT NULL REFERENCES generated_clips(id) ON DELETE CASCADE,
    target_id VARCHAR(36) NOT NULL REFERENCES live_post_targets(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (clip_id, target_id)
);
