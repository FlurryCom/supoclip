-- Ready-made clips from other channels, remade with an edit kit (the "Reformular" page).
-- The recorder submits them one at a time; the worker renders the whole video.
CREATE TABLE IF NOT EXISTS reform_items (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    platform VARCHAR(16) NOT NULL,
    video_id VARCHAR(100) NOT NULL,
    source_url TEXT NOT NULL,
    source_channel VARCHAR(100) NOT NULL,
    title TEXT NOT NULL,
    duration REAL,
    task_id VARCHAR(36) REFERENCES tasks(id) ON DELETE SET NULL,
    edit_kit VARCHAR(32) NOT NULL,
    auto_publish BOOLEAN NOT NULL DEFAULT false,
    target_ids TEXT[] NOT NULL DEFAULT '{}',
    -- pending (waiting for the recorder), submitted (task running), done, error
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, platform, video_id)
);
CREATE INDEX IF NOT EXISTS idx_reform_items_status ON reform_items (status, created_at);
CREATE INDEX IF NOT EXISTS idx_reform_items_task ON reform_items (task_id);
