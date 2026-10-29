-- Isolated business RLS candidate: explicitly applied to a disposable database.
-- Do not apply to an existing installation. It changes RLS on business tables
-- and does not yet support global discovery, bootstrap, or global workers.
CREATE SCHEMA paperpilot_business;
REVOKE ALL ON SCHEMA paperpilot_business FROM PUBLIC;

CREATE TABLE paperpilot_business.project_logins (
    login_role text PRIMARY KEY,
    user_id uuid NOT NULL,
    project_id uuid NOT NULL,
    FOREIGN KEY (project_id, user_id) REFERENCES public.project_members(project_id, user_id) ON DELETE CASCADE
);
REVOKE ALL ON paperpilot_business.project_logins FROM PUBLIC;

CREATE FUNCTION paperpilot_business.bound_project() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $$
    SELECT project_id FROM paperpilot_business.project_logins WHERE login_role = SESSION_USER
$$;
REVOKE ALL ON FUNCTION paperpilot_business.bound_project() FROM PUBLIC;

CREATE FUNCTION paperpilot_business.bound_identity(requested_user uuid, requested_project uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $$
    SELECT EXISTS (SELECT 1 FROM paperpilot_business.project_logins
      WHERE login_role = SESSION_USER AND user_id = requested_user AND project_id = requested_project)
$$;
REVOKE ALL ON FUNCTION paperpilot_business.bound_identity(uuid,uuid) FROM PUBLIC;

DO $$
DECLARE item text;
BEGIN
    FOREACH item IN ARRAY ARRAY[
        'projects', 'project_members', 'papers', 'project_papers', 'authors', 'paper_authors',
        'topics', 'paper_topics', 'chunks', 'embedding_models', 'chunk_embeddings', 'tasks',
        'task_papers', 'task_attempts', 'task_results', 'evidence_fields', 'result_sources',
        'reports', 'report_versions', 'report_version_sources', 'idempotency_records', 'outbox_events'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', item);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', item);
        EXECUTE format('CREATE POLICY project_identity ON public.%I USING (project_id = paperpilot_business.bound_project()) WITH CHECK (project_id = paperpilot_business.bound_project())', item);
    END LOOP;
END $$;
