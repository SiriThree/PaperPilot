-- Reconcile the two released 002/003 branches without changing old checksums.
-- Existing application data and immutable results are retained.
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS disabled boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash bytea PRIMARY KEY CHECK (octet_length(token_hash) = 32),
    user_id uuid NOT NULL REFERENCES users(user_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS auth_sessions_expiry ON auth_sessions(expires_at);

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS recovery_failures integer NOT NULL DEFAULT 0
    CHECK (recovery_failures >= 0);
ALTER TABLE outbox_events
    ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz,
    ADD COLUMN IF NOT EXISTS dead_lettered_at timestamptz,
    ADD COLUMN IF NOT EXISTS retry_attempts integer NOT NULL DEFAULT 0 CHECK (retry_attempts >= 0),
    ADD COLUMN IF NOT EXISTS claim_token uuid,
    ADD COLUMN IF NOT EXISTS delivery_lease_until timestamptz,
    ADD COLUMN IF NOT EXISTS manual_replays integer NOT NULL DEFAULT 0 CHECK (manual_replays >= 0),
    ADD COLUMN IF NOT EXISTS last_replayed_at timestamptz;
CREATE INDEX IF NOT EXISTS outbox_dead_letters ON outbox_events(dead_lettered_at, event_id)
    WHERE dead_lettered_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS outbox_due_live ON outbox_events(next_attempt_at, created_at)
    WHERE published_at IS NULL AND dead_lettered_at IS NULL;

CREATE TABLE IF NOT EXISTS process_indexes (
    task_id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    model_id uuid NOT NULL,
    dimensions integer NOT NULL,
    fingerprint varchar(64) NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    FOREIGN KEY (project_id, task_id) REFERENCES task_results(project_id, task_id),
    FOREIGN KEY (project_id, model_id, dimensions)
        REFERENCES embedding_models(project_id, model_id, dimensions)
);
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='public.process_indexes'::regclass
                   AND tgname='process_indexes_immutable' AND NOT tgisinternal) THEN
        CREATE TRIGGER process_indexes_immutable BEFORE UPDATE OR DELETE ON process_indexes
            FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='public.chunk_embeddings'::regclass
                   AND tgname='chunk_embeddings_immutable' AND NOT tgisinternal) THEN
        CREATE TRIGGER chunk_embeddings_immutable BEFORE UPDATE OR DELETE ON chunk_embeddings
            FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='public.embedding_models'::regclass
                   AND tgname='embedding_models_immutable' AND NOT tgisinternal) THEN
        CREATE TRIGGER embedding_models_immutable BEFORE UPDATE OR DELETE ON embedding_models
            FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
    END IF;
END $$;
