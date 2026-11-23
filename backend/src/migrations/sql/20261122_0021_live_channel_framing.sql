-- Where a channel's webcam and gameplay sit (fractions of the frame), marked by the owner for Cat Clips v2.
-- game_box alone means the streamer has no webcam; both null means the automatic detector.
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS cam_box JSONB;
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS game_box JSONB;
-- A recent frame of the live (JPEG), saved by the clip render, to mark the boxes on.
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS framing_frame BYTEA;
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS framing_frame_at TIMESTAMPTZ;
