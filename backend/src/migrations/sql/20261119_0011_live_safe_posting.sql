-- Safe posting (fork feature): each account waits 15 minutes between posts so
-- clips never go out in a burst. On by default; turning it off shows a warning.
ALTER TABLE live_post_targets ADD COLUMN IF NOT EXISTS safe_posting boolean NOT NULL DEFAULT true;
