-- Sensitive blur (fork feature): find nudity and heavy gore in live clips and blur
-- them per posting account. Both switches default to on; a clip is blurred for an
-- account only when the account and the clip's live channel both have it on.
ALTER TABLE live_channels ADD COLUMN IF NOT EXISTS blur_sensitive boolean NOT NULL DEFAULT true;
ALTER TABLE live_post_targets ADD COLUMN IF NOT EXISTS blur_sensitive boolean NOT NULL DEFAULT true;

-- One analysis per rendered clip file (the worker writes it, the recorder reads it).
CREATE TABLE IF NOT EXISTS live_clip_sensitive (
    file_path text PRIMARY KEY,
    duration double precision NOT NULL,
    regions jsonb NOT NULL DEFAULT '[]',
    error text,
    checked_at timestamptz NOT NULL DEFAULT now()
);

-- When a feature went live; clips made before it keep the old behaviour.
CREATE TABLE IF NOT EXISTS live_feature_marks (
    name text PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO live_feature_marks (name) VALUES ('sensitive_blur') ON CONFLICT DO NOTHING;
