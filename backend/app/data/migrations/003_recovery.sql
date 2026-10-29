ALTER TABLE tasks ADD COLUMN recovery_failures integer NOT NULL DEFAULT 0 CHECK (recovery_failures >= 0);
ALTER TABLE outbox_events
    ADD COLUMN retry_attempts integer NOT NULL DEFAULT 0 CHECK (retry_attempts >= 0),
    ADD COLUMN claim_token uuid,
    ADD COLUMN delivery_lease_until timestamptz,
    ADD COLUMN dead_lettered_at timestamptz,
    ADD COLUMN manual_replays integer NOT NULL DEFAULT 0 CHECK (manual_replays >= 0),
    ADD COLUMN last_replayed_at timestamptz;
CREATE INDEX outbox_due_live ON outbox_events(next_attempt_at,created_at)
    WHERE published_at IS NULL AND dead_lettered_at IS NULL;
