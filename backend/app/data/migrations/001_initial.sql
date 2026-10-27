-- AB-1 formal schema on PostgreSQL 16 + pgvector; source of truth:
-- docs/data-model/04-data-dictionary.md (24 tables) and 03-resolved-model.md (K01-K15).
-- Cross-row rules K02/K04/K08-K11 use deferred constraint triggers, never cross-table CHECKs.
-- pgvector column type is untyped `vector` pending member C's model dimensions (see chunk_embeddings).

CREATE EXTENSION IF NOT EXISTS vector;

-- 1 users (K01)
CREATE TABLE users (
    user_id uuid PRIMARY KEY,
    username varchar(64) NOT NULL UNIQUE CHECK (length(username) BETWEEN 1 AND 64),
    display_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- 2 projects; owner composite FK to members is added after project_members (K02/K03)
CREATE TABLE projects (
    project_id uuid PRIMARY KEY,
    name varchar(100) NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    description varchar(1000) NOT NULL DEFAULT '',
    owner_id uuid NOT NULL REFERENCES users (user_id),
    owner_role text NOT NULL DEFAULT 'owner' CHECK (owner_role = 'owner'),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- 3 project_members
CREATE TABLE project_members (
    project_id uuid NOT NULL REFERENCES projects (project_id),
    user_id uuid NOT NULL REFERENCES users (user_id),
    role text NOT NULL CHECK (role IN ('owner', 'member')),
    joined_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, user_id),
    UNIQUE (project_id, user_id, role)
);
CREATE UNIQUE INDEX one_owner_per_project ON project_members (project_id) WHERE role = 'owner';

ALTER TABLE projects ADD CONSTRAINT projects_owner_is_member
    FOREIGN KEY (project_id, owner_id, owner_role)
    REFERENCES project_members (project_id, user_id, role)
    DEFERRABLE INITIALLY DEFERRED;

-- 4 papers: per-import bibliography metadata; metadata_id is never the public paper_id (C01/C02)
CREATE TABLE papers (
    metadata_id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects (project_id),
    title varchar(200) NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    year integer CHECK (year IS NULL OR year BETWEEN 1000 AND 3000),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, metadata_id)
);

-- 5 project_papers (K04/K06/K07)
CREATE TABLE project_papers (
    paper_id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects (project_id),
    metadata_id uuid NOT NULL UNIQUE,
    imported_by uuid NOT NULL REFERENCES users (user_id),
    digest varchar(64) NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
    storage_key varchar(1024) NOT NULL CHECK (length(storage_key) BETWEEN 1 AND 1024),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'searchable', 'failed')),
    index_state text NOT NULL DEFAULT 'pending'
        CHECK (index_state IN ('pending', 'ready', 'failed', 'mock_only')),
    active_process_task_id uuid,
    removed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT project_papers_paper_unique UNIQUE (project_id, paper_id),
    CONSTRAINT project_papers_digest_unique UNIQUE (project_id, digest)
);
CREATE INDEX live_project_papers ON project_papers (project_id) WHERE removed_at IS NULL;

ALTER TABLE project_papers ADD CONSTRAINT project_papers_metadata
    FOREIGN KEY (project_id, metadata_id) REFERENCES papers (project_id, metadata_id);

-- 6 authors; write path awaits member C (no DataPort input yet)
CREATE TABLE authors (
    author_id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects (project_id),
    name text NOT NULL,
    UNIQUE (project_id, author_id)
);

-- 7 paper_authors
CREATE TABLE paper_authors (
    paper_id uuid NOT NULL,
    author_id uuid NOT NULL,
    project_id uuid NOT NULL,
    author_order integer NOT NULL CHECK (author_order >= 1),
    PRIMARY KEY (paper_id, author_id),
    UNIQUE (paper_id, author_order),
    FOREIGN KEY (project_id, paper_id) REFERENCES project_papers (project_id, paper_id),
    FOREIGN KEY (project_id, author_id) REFERENCES authors (project_id, author_id)
);

-- 8 topics
CREATE TABLE topics (
    topic_id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects (project_id),
    name text NOT NULL,
    UNIQUE (project_id, topic_id),
    UNIQUE (project_id, name)
);

-- 9 paper_topics
CREATE TABLE paper_topics (
    paper_id uuid NOT NULL,
    topic_id uuid NOT NULL,
    project_id uuid NOT NULL,
    PRIMARY KEY (paper_id, topic_id),
    FOREIGN KEY (project_id, paper_id) REFERENCES project_papers (project_id, paper_id),
    FOREIGN KEY (project_id, topic_id) REFERENCES topics (project_id, topic_id)
);

-- 10 chunks; committed content and attribution are immutable (K07)
CREATE TABLE chunks (
    chunk_id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    paper_id uuid NOT NULL,
    page_number integer NOT NULL CHECK (page_number >= 1),
    excerpt varchar(4000) NOT NULL CHECK (length(excerpt) BETWEEN 1 AND 4000),
    content text,
    synthetic boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, chunk_id),
    UNIQUE (project_id, paper_id, chunk_id),
    FOREIGN KEY (project_id, paper_id) REFERENCES project_papers (project_id, paper_id)
);

-- 11 embedding_models; A/C proposal, not a chosen model (C12/K15)
CREATE TABLE embedding_models (
    model_id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects (project_id),
    provider text NOT NULL,
    model_name text NOT NULL,
    revision text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions > 0),
    distance_metric text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, provider, model_name, revision),
    UNIQUE (project_id, model_id, dimensions)
);

-- 12 chunk_embeddings; per-model versioned dimensions (K15)
CREATE TABLE chunk_embeddings (
    chunk_id uuid NOT NULL,
    model_id uuid NOT NULL,
    project_id uuid NOT NULL,
    dimensions integer NOT NULL,
    embedding vector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, model_id),
    FOREIGN KEY (project_id, chunk_id) REFERENCES chunks (project_id, chunk_id),
    FOREIGN KEY (project_id, model_id, dimensions)
        REFERENCES embedding_models (project_id, model_id, dimensions),
    CHECK (vector_dims(embedding) = dimensions)
);

-- 13 tasks (K08)
CREATE TABLE tasks (
    task_id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects (project_id),
    actor_id uuid NOT NULL REFERENCES users (user_id),
    request_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('process', 'answer', 'evidence', 'draft')),
    status text NOT NULL CHECK (status IN ('pending', 'processing', 'succeeded', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    payload jsonb NOT NULL,
    payload_fingerprint varchar(64) NOT NULL CHECK (payload_fingerprint ~ '^[0-9a-f]{64}$'),
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, task_id),
    UNIQUE (project_id, task_id, kind),
    CHECK ((status = 'failed') = (error_code IS NOT NULL AND error_message IS NOT NULL)),
    CHECK (status <> 'processing' OR attempts >= 1)
);
CREATE INDEX pending_task_claim ON tasks (created_at, task_id) WHERE status = 'pending';

-- 14 task_papers: explicit paper scope snapshot
CREATE TABLE task_papers (
    task_id uuid NOT NULL,
    paper_id uuid NOT NULL,
    project_id uuid NOT NULL,
    PRIMARY KEY (task_id, paper_id),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks (project_id, task_id),
    FOREIGN KEY (project_id, paper_id) REFERENCES project_papers (project_id, paper_id)
);

-- 15 task_attempts: one processing attempt per task; lease recovery expires the old row first (K08)
CREATE TABLE task_attempts (
    task_id uuid NOT NULL,
    attempt_no integer NOT NULL CHECK (attempt_no >= 1),
    project_id uuid NOT NULL,
    actor_id uuid NOT NULL REFERENCES users (user_id),
    request_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('processing', 'succeeded', 'failed', 'expired')),
    claimed_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz NOT NULL,
    finished_at timestamptz,
    error_code text,
    error_message text,
    PRIMARY KEY (task_id, attempt_no),
    UNIQUE (project_id, task_id, attempt_no),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks (project_id, task_id),
    CHECK (lease_expires_at > claimed_at),
    CHECK ((status = 'processing') = (finished_at IS NULL))
);
CREATE UNIQUE INDEX one_processing_attempt ON task_attempts (task_id) WHERE status = 'processing';
CREATE INDEX lease_recovery ON task_attempts (lease_expires_at) WHERE status = 'processing';

-- 16 task_results: one stable container per task; evidence lives here plus typed field rows (K08/K09)
CREATE TABLE task_results (
    task_id uuid PRIMARY KEY REFERENCES tasks (task_id),
    project_id uuid NOT NULL,
    kind text NOT NULL,
    committed_attempt integer NOT NULL CHECK (committed_attempt >= 1),
    paper_id uuid,
    evidence_id uuid,
    payload jsonb NOT NULL,
    result_fingerprint varchar(64) NOT NULL CHECK (result_fingerprint ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, task_id),
    UNIQUE (project_id, paper_id, task_id),
    FOREIGN KEY (project_id, task_id, kind) REFERENCES tasks (project_id, task_id, kind),
    FOREIGN KEY (project_id, task_id, committed_attempt)
        REFERENCES task_attempts (project_id, task_id, attempt_no),
    FOREIGN KEY (project_id, paper_id) REFERENCES project_papers (project_id, paper_id),
    CHECK (
        (kind = 'process' AND paper_id IS NOT NULL AND evidence_id IS NULL)
        OR (kind = 'evidence' AND paper_id IS NOT NULL AND evidence_id IS NOT NULL)
        OR (kind IN ('answer', 'draft') AND paper_id IS NULL AND evidence_id IS NULL)
    )
);
CREATE UNIQUE INDEX evidence_id_global ON task_results (evidence_id) WHERE evidence_id IS NOT NULL;
CREATE INDEX evidence_by_paper ON task_results (project_id, paper_id) WHERE kind = 'evidence';

-- 17 evidence_fields: exactly five rows per evidence result, enforced by K10 trigger
CREATE TABLE evidence_fields (
    task_id uuid NOT NULL,
    field_name text NOT NULL
        CHECK (field_name IN ('research_question', 'method', 'results', 'conclusion', 'limitations')),
    project_id uuid NOT NULL,
    text text,
    status text NOT NULL
        CHECK (status IN ('not_mentioned', 'insufficient_evidence', 'pending_verification')),
    PRIMARY KEY (task_id, field_name),
    UNIQUE (project_id, task_id, field_name),
    FOREIGN KEY (project_id, task_id) REFERENCES task_results (project_id, task_id),
    CHECK (status <> 'pending_verification' OR (text IS NOT NULL AND length(text) > 0))
);

-- 18 result_sources: root scope for process/answer/draft, five field scopes for evidence (C05/K10)
CREATE TABLE result_sources (
    task_id uuid NOT NULL,
    scope text NOT NULL,
    citation_id varchar(32) NOT NULL CHECK (length(citation_id) BETWEEN 1 AND 32),
    project_id uuid NOT NULL,
    field_name text,
    ordinal integer NOT NULL CHECK (ordinal >= 1),
    paper_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    page_number integer NOT NULL CHECK (page_number >= 1),
    excerpt varchar(4000) NOT NULL CHECK (length(excerpt) BETWEEN 1 AND 4000),
    synthetic boolean NOT NULL,
    PRIMARY KEY (task_id, scope, citation_id),
    UNIQUE (task_id, scope, ordinal),
    FOREIGN KEY (project_id, task_id) REFERENCES task_results (project_id, task_id),
    FOREIGN KEY (project_id, paper_id, chunk_id) REFERENCES chunks (project_id, paper_id, chunk_id),
    FOREIGN KEY (project_id, task_id, field_name)
        REFERENCES evidence_fields (project_id, task_id, field_name),
    CHECK ((field_name IS NULL) = (scope = 'root')),
    CHECK (field_name IS NULL OR scope = field_name)
);
CREATE INDEX result_sources_parent ON result_sources (project_id, task_id, scope, ordinal);

-- 19 reports; current-version composite FK added after report_versions (K11)
CREATE TABLE reports (
    report_id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects (project_id),
    created_by uuid NOT NULL REFERENCES users (user_id),
    current_version_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, report_id)
);

-- 20 report_versions; immutable chain, first version is number 1 with NULL base (K11)
CREATE TABLE report_versions (
    version_id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    report_id uuid NOT NULL,
    number integer NOT NULL CHECK (number >= 1),
    base_version_id uuid,
    title varchar(200) NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    research_question varchar(2000) NOT NULL CHECK (length(research_question) BETWEEN 1 AND 2000),
    body text NOT NULL CHECK (length(body) BETWEEN 1 AND 100000),
    created_by uuid NOT NULL REFERENCES users (user_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (report_id, number),
    UNIQUE (project_id, version_id),
    UNIQUE (project_id, report_id, version_id),
    FOREIGN KEY (project_id, report_id) REFERENCES reports (project_id, report_id),
    FOREIGN KEY (project_id, report_id, base_version_id)
        REFERENCES report_versions (project_id, report_id, version_id),
    CHECK ((number = 1 AND base_version_id IS NULL) OR (number > 1 AND base_version_id IS NOT NULL))
);
CREATE UNIQUE INDEX one_child_per_base ON report_versions (report_id, base_version_id)
    WHERE base_version_id IS NOT NULL;

ALTER TABLE reports ADD CONSTRAINT reports_current_version
    FOREIGN KEY (project_id, report_id, current_version_id)
    REFERENCES report_versions (project_id, report_id, version_id)
    DEFERRABLE INITIALLY DEFERRED;

-- 21 report_version_sources: frozen snapshot; reads are authorized by project, not by paper liveness
CREATE TABLE report_version_sources (
    version_id uuid NOT NULL,
    citation_id varchar(32) NOT NULL CHECK (length(citation_id) BETWEEN 1 AND 32),
    project_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 1),
    paper_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    page_number integer NOT NULL CHECK (page_number >= 1),
    excerpt varchar(4000) NOT NULL CHECK (length(excerpt) BETWEEN 1 AND 4000),
    synthetic boolean NOT NULL,
    PRIMARY KEY (version_id, citation_id),
    UNIQUE (version_id, ordinal),
    FOREIGN KEY (project_id, version_id) REFERENCES report_versions (project_id, version_id),
    FOREIGN KEY (project_id, paper_id, chunk_id) REFERENCES chunks (project_id, paper_id, chunk_id)
);

-- 22 idempotency_records (C10/K12); demo policy keeps records indefinitely, so expires_at stays NULL
CREATE TABLE idempotency_records (
    user_id uuid NOT NULL REFERENCES users (user_id),
    project_id uuid NOT NULL REFERENCES projects (project_id),
    operation text NOT NULL
        CHECK (operation IN ('upload', 'process', 'answer', 'evidence', 'draft', 'report-save')),
    key varchar(128) NOT NULL CHECK (key ~ '^[A-Za-z0-9_-]{1,128}$'),
    request_fingerprint varchar(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    response_snapshot jsonb NOT NULL,
    paper_id uuid,
    task_id uuid,
    version_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    PRIMARY KEY (user_id, project_id, operation, key),
    FOREIGN KEY (project_id, paper_id) REFERENCES project_papers (project_id, paper_id),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks (project_id, task_id),
    FOREIGN KEY (project_id, version_id) REFERENCES report_versions (project_id, version_id),
    CHECK (expires_at IS NULL),
    CHECK (
        (operation = 'upload' AND paper_id IS NOT NULL AND task_id IS NOT NULL AND version_id IS NULL)
        OR (
            operation IN ('process', 'answer', 'evidence', 'draft')
            AND paper_id IS NULL
            AND task_id IS NOT NULL
            AND version_id IS NULL
        )
        OR (operation = 'report-save' AND paper_id IS NULL AND task_id IS NULL AND version_id IS NOT NULL)
    )
);

-- 23 audit_events: append-only; fingerprint dedupes event_id replays (K13/K14)
CREATE TABLE audit_events (
    event_id uuid PRIMARY KEY,
    timestamp timestamptz NOT NULL,
    request_id uuid NOT NULL,
    actor_id uuid REFERENCES users (user_id),
    project_id uuid REFERENCES projects (project_id),
    action text NOT NULL,
    resource_type text NOT NULL,
    decision text NOT NULL,
    execution_status text NOT NULL,
    policy_id text,
    duration_ms double precision NOT NULL CHECK (
        duration_ms >= 0 AND duration_ms <> 'NaN'::float8 AND duration_ms <> 'Infinity'::float8
    ),
    mock boolean NOT NULL,
    event_fingerprint varchar(64) NOT NULL CHECK (event_fingerprint ~ '^[0-9a-f]{64}$')
);
CREATE INDEX project_audit_page ON audit_events (project_id, timestamp DESC, event_id DESC);

-- 24 outbox_events: same-transaction domain events; delivery columns alone are mutable (C13)
CREATE TABLE outbox_events (
    event_id uuid PRIMARY KEY,
    project_id uuid REFERENCES projects (project_id),
    actor_id uuid REFERENCES users (user_id),
    request_id uuid NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    delivery_attempts integer NOT NULL DEFAULT 0 CHECK (delivery_attempts >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    last_error_code text
);
CREATE INDEX outbox_redelivery ON outbox_events (next_attempt_at, created_at) WHERE published_at IS NULL;

-- K04: each papers row is paired with exactly one project import at commit
CREATE FUNCTION check_paper_metadata_pair() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM project_papers pp WHERE pp.metadata_id = NEW.metadata_id) THEN
        RAISE EXCEPTION 'paper metadata has no project import';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER papers_metadata_paired AFTER INSERT ON papers
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_paper_metadata_pair();

-- K03: project owner is immutable
CREATE FUNCTION forbid_owner_transfer() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'project owner is immutable';
END $$;
CREATE TRIGGER projects_owner_immutable BEFORE UPDATE OF owner_id, owner_role ON projects
    FOR EACH ROW EXECUTE FUNCTION forbid_owner_transfer();

-- K04/K07: active pointer must reference a same-paper process result; searchable needs the pointer
CREATE FUNCTION check_project_paper_state() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.active_process_task_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM task_results tr
        WHERE tr.project_id = NEW.project_id
          AND tr.task_id = NEW.active_process_task_id
          AND tr.kind = 'process'
          AND tr.paper_id = NEW.paper_id
    ) THEN
        RAISE EXCEPTION 'active pointer must reference a process result of the same paper';
    END IF;
    IF NEW.status = 'searchable' AND NEW.active_process_task_id IS NULL THEN
        RAISE EXCEPTION 'searchable paper requires an active process result';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER project_paper_state AFTER INSERT OR UPDATE ON project_papers
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_project_paper_state();

-- K08/K09: succeeded task <=> exactly one same-kind result committed by its succeeded attempt
CREATE FUNCTION check_task_result_pair() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'task_results' THEN
        IF NOT EXISTS (
            SELECT 1 FROM tasks t
            WHERE t.task_id = NEW.task_id AND t.status = 'succeeded' AND t.kind = NEW.kind
        ) THEN
            RAISE EXCEPTION 'task result requires a succeeded task of the same kind';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM task_attempts ta
            WHERE ta.project_id = NEW.project_id
              AND ta.task_id = NEW.task_id
              AND ta.attempt_no = NEW.committed_attempt
              AND ta.status = 'succeeded'
        ) THEN
            RAISE EXCEPTION 'task result must reference its succeeded attempt';
        END IF;
        RETURN NULL;
    END IF;
    IF NEW.status = 'succeeded' THEN
        IF NOT EXISTS (SELECT 1 FROM task_results tr WHERE tr.task_id = NEW.task_id) THEN
            RAISE EXCEPTION 'succeeded task requires one committed result';
        END IF;
    ELSIF EXISTS (SELECT 1 FROM task_results tr WHERE tr.task_id = NEW.task_id) THEN
        RAISE EXCEPTION 'non-succeeded task must not retain a result';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER task_result_inserted AFTER INSERT ON task_results
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_task_result_pair();
CREATE CONSTRAINT TRIGGER task_status_changed AFTER UPDATE OF status ON tasks
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_task_result_pair();

-- K10: evidence results have exactly five typed fields; pending fields keep supporting sources
CREATE FUNCTION check_evidence_shape() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'evidence_fields' THEN
        IF NOT EXISTS (
            SELECT 1 FROM task_results tr WHERE tr.task_id = NEW.task_id AND tr.kind = 'evidence'
        ) THEN
            RAISE EXCEPTION 'evidence field requires an evidence result';
        END IF;
        IF NEW.status = 'pending_verification' AND NOT EXISTS (
            SELECT 1 FROM result_sources rs
            WHERE rs.task_id = NEW.task_id AND rs.field_name = NEW.field_name
        ) THEN
            RAISE EXCEPTION 'pending verification field requires supporting sources';
        END IF;
        RETURN NULL;
    END IF;
    IF NEW.kind = 'evidence' THEN
        IF (SELECT count(*) FROM evidence_fields ef WHERE ef.task_id = NEW.task_id) <> 5 THEN
            RAISE EXCEPTION 'evidence result requires exactly five typed fields';
        END IF;
    ELSIF EXISTS (SELECT 1 FROM evidence_fields ef WHERE ef.task_id = NEW.task_id) THEN
        RAISE EXCEPTION 'only evidence results carry evidence fields';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER evidence_field_inserted AFTER INSERT ON evidence_fields
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_evidence_shape();
CREATE CONSTRAINT TRIGGER evidence_result_inserted AFTER INSERT ON task_results
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_evidence_shape();

-- K11: versions continue from the previous number; the report pointer stays on the newest version.
-- Deferred row triggers see the row as of the event, so the reports branch re-reads the final
-- committed-in-transaction pointer instead of NEW (a same-transaction version bump would otherwise
-- fail the stale INSERT event).
CREATE FUNCTION check_report_chain() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    pointer uuid;
BEGIN
    IF TG_TABLE_NAME = 'report_versions' THEN
        IF NEW.number = 1 THEN
            -- UNIQUE(report_id, number) already blocks duplicates; re-check at commit for defense.
            IF EXISTS (
                SELECT 1 FROM report_versions rv
                WHERE rv.report_id = NEW.report_id
                  AND rv.number = 1
                  AND rv.version_id <> NEW.version_id
            ) THEN
                RAISE EXCEPTION 'report may have only one first version';
            END IF;
        ELSIF NOT EXISTS (
            SELECT 1 FROM report_versions rv
            WHERE rv.report_id = NEW.report_id
              AND rv.number = NEW.number - 1
              AND rv.version_id = NEW.base_version_id
        ) THEN
            RAISE EXCEPTION 'report version must continue from the previous version';
        END IF;
        RETURN NULL;
    END IF;
    SELECT current_version_id INTO pointer FROM reports
        WHERE project_id = NEW.project_id AND report_id = NEW.report_id;
    IF NOT EXISTS (
        SELECT 1 FROM report_versions rv
        WHERE rv.report_id = NEW.report_id
          AND rv.version_id = pointer
          AND rv.number = (SELECT max(number) FROM report_versions WHERE report_id = NEW.report_id)
    ) THEN
        RAISE EXCEPTION 'report current version must be the newest version';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER report_version_chained AFTER INSERT ON report_versions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_report_chain();
CREATE CONSTRAINT TRIGGER report_pointer_current AFTER INSERT OR UPDATE OF current_version_id ON reports
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_report_chain();

-- Shared guard: historical rows are insert-only; only outbox delivery columns may change
CREATE FUNCTION forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is immutable through the business entry', TG_TABLE_NAME;
END $$;

CREATE TRIGGER chunks_immutable
    BEFORE UPDATE OF page_number, excerpt, content, synthetic, project_id, paper_id ON chunks
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER chunks_no_delete BEFORE DELETE ON chunks
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER report_versions_immutable BEFORE UPDATE OR DELETE ON report_versions
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER report_version_sources_immutable BEFORE UPDATE OR DELETE ON report_version_sources
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER outbox_no_delete BEFORE DELETE ON outbox_events
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE FUNCTION protect_outbox_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.event_id IS DISTINCT FROM OLD.event_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.request_id IS DISTINCT FROM OLD.request_id
       OR NEW.payload IS DISTINCT FROM OLD.payload
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'outbox identity and payload are immutable';
    END IF;
    -- BEFORE triggers must return NEW: RETURN NULL would silently cancel the row update.
    RETURN NEW;
END $$;
CREATE TRIGGER outbox_identity_protected BEFORE UPDATE ON outbox_events
    FOR EACH ROW EXECUTE FUNCTION protect_outbox_identity();
