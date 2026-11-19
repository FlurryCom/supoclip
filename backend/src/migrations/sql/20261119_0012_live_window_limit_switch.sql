-- The owner switches the per-channel daily window limit on or off and picks the
-- number on the Live page. Owner's call: 48 windows a day (was 12).
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS limit_windows boolean NOT NULL DEFAULT true;
ALTER TABLE live_channels ALTER COLUMN daily_window_cap SET DEFAULT 48;
UPDATE live_channels SET daily_window_cap = 48 WHERE daily_window_cap = 12;
