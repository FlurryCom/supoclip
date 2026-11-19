-- Live clipping: post kept live clips to social accounts through Postiz
-- (fork feature, docs/live-clipping.md#posting). Only accounts the user
-- explicitly authorized receive posts. Each authorization keeps the Postiz
-- integration's provider and name; before every post the recorder checks that
-- the integration id still has them, and refuses to post if not.
CREATE TABLE IF NOT EXISTS live_post_targets (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    integration_id VARCHAR(100) NOT NULL,
    provider VARCHAR(32) NOT NULL CHECK (provider IN ('tiktok', 'youtube', 'instagram', 'instagram-standalone')),
    name VARCHAR(255) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    -- Clips need at least this final score to be posted here.
    min_score INTEGER NOT NULL DEFAULT 80 CHECK (min_score BETWEEN 0 AND 100),
    -- draft: lands in Postiz for approval. now: published right away.
    mode VARCHAR(8) NOT NULL DEFAULT 'draft' CHECK (mode IN ('draft', 'now')),
    daily_cap INTEGER NOT NULL DEFAULT 10 CHECK (daily_cap BETWEEN 1 AND 100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, integration_id)
);

CREATE TABLE IF NOT EXISTS live_posts (
    id VARCHAR(36) PRIMARY KEY,
    clip_id VARCHAR(36) REFERENCES generated_clips(id) ON DELETE SET NULL,
    target_id VARCHAR(36) NOT NULL REFERENCES live_post_targets(id) ON DELETE CASCADE,
    -- posted (draft or published in Postiz), refused (safety check failed), error
    status VARCHAR(16) NOT NULL,
    postiz_post_id VARCHAR(100),
    detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (clip_id, target_id)
);
CREATE INDEX IF NOT EXISTS idx_live_posts_target_day ON live_posts (target_id, created_at);
