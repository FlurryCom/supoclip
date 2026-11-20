-- Each posting account sets the score a live moment needs to be rendered at all
-- (owner: 80 for Cat Clips). While any account is active its lowest value wins
-- over the live channel's own minimum, so no clip is rendered just to be dropped.
ALTER TABLE live_post_targets ADD COLUMN IF NOT EXISTS process_min_score integer NOT NULL DEFAULT 80
    CHECK (process_min_score BETWEEN 0 AND 100);
