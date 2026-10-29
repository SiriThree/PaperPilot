-- Explicit administrator install after 001_statistics_views.sql; never adopt unknown roles.
CREATE TABLE paperpilot_security.service_logins (
    login_role name PRIMARY KEY,
    role_oid oid NOT NULL UNIQUE,
    kind text NOT NULL CHECK (kind IN ('reader', 'audit')),
    project_id uuid REFERENCES public.projects(project_id),
    revoked_at timestamptz,
    CHECK ((kind = 'reader') = (project_id IS NOT NULL))
);
REVOKE ALL ON paperpilot_security.service_logins FROM PUBLIC;
