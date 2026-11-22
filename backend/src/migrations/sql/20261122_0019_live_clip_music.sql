-- Copyright music check per rendered clip file (media/music_check): clean, removed, left or error.
-- The recorder never posts a clip whose status is "left" (music still heard after the cleanup).
CREATE TABLE IF NOT EXISTS live_clip_music (
    file_path VARCHAR(500) PRIMARY KEY,
    status VARCHAR(8) NOT NULL,
    music_seconds REAL NOT NULL DEFAULT 0,
    error TEXT,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
