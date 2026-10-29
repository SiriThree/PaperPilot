-- Isolated candidate only. Apply explicitly after 005 in a disposable database.
-- The function owner needs only SELECT (user_id, disabled) on public.users and
-- SELECT on project_logins. Deployment must independently provision and review it.
CREATE OR REPLACE FUNCTION paperpilot_business.bound_project() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $$
    SELECT p.project_id FROM paperpilot_business.project_logins p
    JOIN public.users u ON u.user_id = p.user_id
    WHERE p.login_role = SESSION_USER AND NOT u.disabled
$$;
REVOKE ALL ON FUNCTION paperpilot_business.bound_project() FROM PUBLIC;

CREATE OR REPLACE FUNCTION paperpilot_business.bound_identity(requested_user uuid, requested_project uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $$
    SELECT EXISTS (SELECT 1 FROM paperpilot_business.project_logins p
      JOIN public.users u ON u.user_id = p.user_id
      WHERE p.login_role = SESSION_USER AND p.user_id = requested_user
        AND p.project_id = requested_project AND NOT u.disabled)
$$;
REVOKE ALL ON FUNCTION paperpilot_business.bound_identity(uuid,uuid) FROM PUBLIC;
