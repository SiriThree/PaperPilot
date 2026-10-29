-- Operator requeues preserve event identity and append immutable reconciliation history.
ALTER TABLE outbox_events ADD COLUMN retry_base_attempts integer NOT NULL DEFAULT 0
    CHECK (retry_base_attempts >= 0 AND retry_base_attempts <= delivery_attempts);
CREATE TABLE outbox_requeue_history (
    requeue_id uuid PRIMARY KEY,
    event_id uuid NOT NULL REFERENCES outbox_events(event_id),
    requested_at timestamptz NOT NULL DEFAULT now(),
    database_user name NOT NULL,
    prior_attempts integer NOT NULL,
    prior_error_code text,
    prior_dead_lettered_at timestamptz NOT NULL,
    unknown_reconciled boolean NOT NULL
);
CREATE INDEX outbox_requeue_history_event ON outbox_requeue_history(event_id, requested_at);
CREATE TRIGGER outbox_requeue_history_immutable BEFORE UPDATE OR DELETE ON outbox_requeue_history
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
