-- The automatic framing check (Claude looks at a frame). On the channel: when it changed the boxes or
-- found them wrong (framing_notice: 'changed' or 'attention'), why, the owner's boxes before the first
-- automatic change (for "Undo"), and whether it may change the boxes at all (off after "Undo").
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS framing_changed_at TIMESTAMPTZ;
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS framing_notice TEXT;
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS framing_reason TEXT;
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS framing_previous JSONB;
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS framing_auto BOOLEAN NOT NULL DEFAULT true;
-- Per live: when Claude last checked (at the start, then at most every 30 min while the local check
-- fails), whether a check is still owed (a "starting soon" screen or an error: retried 10 min later), and
-- how many checks ran (capped, so a live never calls Claude on every clip).
ALTER TABLE live_recordings ADD COLUMN IF NOT EXISTS framing_checked_at TIMESTAMPTZ;
ALTER TABLE live_recordings ADD COLUMN IF NOT EXISTS framing_check_pending BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE live_recordings ADD COLUMN IF NOT EXISTS framing_check_tries INTEGER NOT NULL DEFAULT 0;
