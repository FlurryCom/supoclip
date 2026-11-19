-- Live clipping: follow YouTube channels too. ``channel`` holds the handle
-- (``@name``) or ``channel/UC...`` for YouTube, the login for Twitch. YouTube
-- chat isn't read yet, so YouTube windows are never filtered by chat.
ALTER TABLE live_channels DROP CONSTRAINT IF EXISTS live_channels_platform_check;
ALTER TABLE live_channels ADD CONSTRAINT live_channels_platform_check CHECK (platform IN ('twitch', 'youtube'));
