-- How many lives an account records at once (the Live page sets it; no row means 10).
-- The recorder caps it at LIVE_MAX_RECORDINGS, the machine's ceiling, and shares slots fairly between accounts.
CREATE TABLE IF NOT EXISTS live_accounts (
    user_id VARCHAR PRIMARY KEY,
    max_recordings INTEGER NOT NULL DEFAULT 10,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
