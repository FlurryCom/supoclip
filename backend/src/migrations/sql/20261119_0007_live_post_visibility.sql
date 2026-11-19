-- Live clipping: choose per account whether clips are posted publicly or
-- privately (only the owner sees them). TikTok apps that Postiz connects
-- without TikTok's audit can only post privately, so existing TikTok accounts
-- start private; the guard keeps a re-run from overriding a later choice.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'live_post_targets' AND column_name = 'visibility'
    ) THEN
        ALTER TABLE live_post_targets
            ADD COLUMN visibility VARCHAR(8) NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'private'));
        UPDATE live_post_targets SET visibility = 'private' WHERE provider = 'tiktok';
    END IF;
END $$;
