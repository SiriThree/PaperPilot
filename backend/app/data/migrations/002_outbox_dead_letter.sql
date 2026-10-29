-- Preserve event identity and payload; no automatic deletion or requeue.
ALTER TABLE outbox_events ADD COLUMN last_attempt_at timestamptz;
ALTER TABLE outbox_events ADD COLUMN dead_lettered_at timestamptz;
CREATE INDEX outbox_dead_letters ON outbox_events (dead_lettered_at, event_id)
    WHERE dead_lettered_at IS NOT NULL;
