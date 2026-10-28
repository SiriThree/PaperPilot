-- D-side additive contract over A's 001_initial.sql. Explicit administrator install only.
-- Fail if the schema already exists: do not overwrite unknown views, bindings or grants.
CREATE SCHEMA paperpilot_security;
REVOKE ALL ON SCHEMA paperpilot_security FROM PUBLIC;

CREATE TABLE paperpilot_security.project_readers (
    login_role name PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES public.projects(project_id)
);
REVOKE ALL ON paperpilot_security.project_readers FROM PUBLIC;

-- Bind to the actual database login, not a caller-settable project GUC or SET ROLE.
-- View-owner permissions allow these joins without granting readers base-table access.
CREATE VIEW paperpilot_security.paper_statistics WITH (security_barrier = true) AS
SELECT pp.project_id, pp.paper_id, p.title, p.year,
       EXISTS (
           SELECT 1 FROM public.task_results tr
           WHERE tr.project_id = pp.project_id AND tr.paper_id = pp.paper_id AND tr.kind = 'evidence'
       ) AS has_evidence
FROM public.project_papers pp
JOIN public.papers p ON p.project_id = pp.project_id AND p.metadata_id = pp.metadata_id
WHERE pp.removed_at IS NULL
  AND EXISTS (
      SELECT 1 FROM paperpilot_security.project_readers r
      WHERE r.login_role = SESSION_USER AND r.project_id = pp.project_id
  );

CREATE VIEW paperpilot_security.paper_topics WITH (security_barrier = true) AS
SELECT pp.project_id, pp.paper_id, t.topic_id, t.name AS topic
FROM public.project_papers pp
JOIN public.paper_topics pt ON pt.project_id = pp.project_id AND pt.paper_id = pp.paper_id
JOIN public.topics t ON t.project_id = pt.project_id AND t.topic_id = pt.topic_id
WHERE pp.removed_at IS NULL
  AND EXISTS (
      SELECT 1 FROM paperpilot_security.project_readers r
      WHERE r.login_role = SESSION_USER AND r.project_id = pp.project_id
  );

REVOKE ALL ON paperpilot_security.paper_statistics, paperpilot_security.paper_topics FROM PUBLIC;
-- Provision fresh NOSUPERUSER/NOBYPASSRLS/NOINHERIT login roles and insert their
-- project binding separately; grant only schema USAGE and SELECT on these two views.
-- Do not grant reads/writes on project_readers, base tables, audit_events or outbox_events.
