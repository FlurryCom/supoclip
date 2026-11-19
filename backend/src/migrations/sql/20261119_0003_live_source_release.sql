-- Live clipping: a window's uploaded copy of the broadcast (1-2 GB) is deleted
-- once its clips are rendered and scored. Rendered clips stay; the window can no
-- longer be regenerated. See docs/live-clipping.md.
ALTER TABLE live_windows ADD COLUMN IF NOT EXISTS source_released BOOLEAN NOT NULL DEFAULT false;
