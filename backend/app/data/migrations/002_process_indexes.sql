-- One immutable index commit per process result, in the same transaction.
CREATE TABLE process_indexes (
    task_id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    model_id uuid NOT NULL,
    dimensions integer NOT NULL,
    fingerprint varchar(64) NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    FOREIGN KEY (project_id, task_id) REFERENCES task_results(project_id, task_id),
    FOREIGN KEY (project_id, model_id, dimensions)
        REFERENCES embedding_models(project_id, model_id, dimensions)
);
CREATE TRIGGER process_indexes_immutable BEFORE UPDATE OR DELETE ON process_indexes
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER chunk_embeddings_immutable BEFORE UPDATE OR DELETE ON chunk_embeddings
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER embedding_models_immutable BEFORE UPDATE OR DELETE ON embedding_models
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
