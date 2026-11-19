-- The owner switches each posting account's daily post limit on or off and picks
-- the number on the Live page. Off by default: no daily post limit, only the
-- 15-minute safe-posting gap.
ALTER TABLE live_post_targets ADD COLUMN IF NOT EXISTS limit_posts boolean NOT NULL DEFAULT false;
