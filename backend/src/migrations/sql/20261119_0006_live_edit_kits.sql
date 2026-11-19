-- Edit kits (fork feature): each live channel picks the look of its clips, and
-- chat buckets keep a sample of showable chat lines (text only, no author).
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS edit_kit text NOT NULL DEFAULT 'none';
ALTER TABLE live_chat_buckets ADD COLUMN IF NOT EXISTS messages jsonb;
