-- A channel that is live but not being recorded yet (all recording slots busy,
-- or too little disk) is shown as "waiting" instead of "offline".
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS waiting_since timestamptz;
