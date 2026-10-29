-- Isolated candidate only: this script is NOT included in the application
-- migration runner and does not change or grant access to the existing audit
-- interface. Install explicitly only in a designated test database.
--
-- The operator must provision a separate NOLOGIN owner with just SELECT on the
-- four referenced tables, transfer ownership of the view/function to that
-- owner, and grant USAGE/SELECT/EXECUTE only to dedicated reader logins.
-- Never grant readers access to this binding table or the audit base table.
CREATE TABLE paperpilot_audit.candidate_readers (
    login_role name PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES public.projects(project_id),
    user_id uuid NOT NULL REFERENCES public.users(user_id)
);
REVOKE ALL ON paperpilot_audit.candidate_readers FROM PUBLIC;

CREATE VIEW paperpilot_audit.candidate_project_events WITH (security_barrier = true) AS
SELECT e.event_id, e.timestamp, e.request_id, e.actor_id, e.project_id, e.action,
       e.resource_type, e.decision, e.execution_status, e.policy_id, e.duration_ms, e.mock, e.details
FROM public.audit_events e
WHERE EXISTS (
    SELECT 1
    FROM paperpilot_audit.candidate_readers r
    JOIN public.project_members m ON m.project_id = r.project_id AND m.user_id = r.user_id
    JOIN public.users u ON u.user_id = r.user_id
    WHERE r.login_role = SESSION_USER AND r.project_id = e.project_id
      AND m.role = 'owner' AND NOT u.disabled
);
REVOKE ALL ON paperpilot_audit.candidate_project_events FROM PUBLIC;

-- An empty view alone cannot distinguish no events from a missing/wrong binding.
-- Call this on every request with the authenticated server-side user/project.
CREATE FUNCTION paperpilot_audit.candidate_binding_matches(expected_user uuid, expected_project uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM paperpilot_audit.candidate_readers r
        JOIN public.project_members m ON m.project_id = r.project_id AND m.user_id = r.user_id
        JOIN public.users u ON u.user_id = r.user_id
        WHERE r.login_role = SESSION_USER AND r.project_id = expected_project
          AND r.user_id = expected_user AND m.role = 'owner' AND NOT u.disabled
    )
$$;
REVOKE ALL ON FUNCTION paperpilot_audit.candidate_binding_matches(uuid, uuid) FROM PUBLIC;
