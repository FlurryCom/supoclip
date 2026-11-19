-- Live clipping: how much to trust a source channel. "default" follows each
-- posting account's own visibility; "unlisted" and "private" hold this
-- channel's clips back on every account (the more restrictive setting wins).
ALTER TABLE live_channels
    ADD COLUMN IF NOT EXISTS post_visibility VARCHAR(8) NOT NULL DEFAULT 'default'
    CHECK (post_visibility IN ('default', 'unlisted', 'private'));
