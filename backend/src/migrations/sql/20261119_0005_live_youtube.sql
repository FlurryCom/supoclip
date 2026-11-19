-- Live clipping: follow YouTube channels too. ``channel`` holds the handle
-- (``@name``) or ``channel/UC...`` for YouTube, the login for Twitch.
ALTER TABLE live_channels DROP CONSTRAINT IF EXISTS live_channels_platform_check;
ALTER TABLE live_channels ADD CONSTRAINT live_channels_platform_check CHECK (platform IN ('twitch', 'youtube'));
