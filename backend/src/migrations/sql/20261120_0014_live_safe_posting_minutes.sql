-- Safe posting minutes: each posting account picks its gap between posts (15 by
-- default), and a live channel can also space its own clips on each account
-- (off by default).
ALTER TABLE live_post_targets ADD COLUMN IF NOT EXISTS safe_gap_minutes integer NOT NULL DEFAULT 15
    CHECK (safe_gap_minutes BETWEEN 1 AND 240);
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS safe_posting boolean NOT NULL DEFAULT false;
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS safe_gap_minutes integer NOT NULL DEFAULT 15
    CHECK (safe_gap_minutes BETWEEN 1 AND 240);
