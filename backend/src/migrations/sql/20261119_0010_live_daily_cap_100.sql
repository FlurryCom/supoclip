-- Owner's call: up to 100 posts a day per account (was 10).
ALTER TABLE live_post_targets ALTER COLUMN daily_cap SET DEFAULT 100;
UPDATE live_post_targets SET daily_cap = 100 WHERE daily_cap = 10;
