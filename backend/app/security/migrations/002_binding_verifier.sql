-- Candidate for isolated tests only. A/D must review owner, grants and deployment.
-- Install explicitly after 001; never grant access to project_readers to the caller.
CREATE FUNCTION paperpilot_security.binding_matches(expected_project uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT EXISTS (SELECT 1 FROM paperpilot_security.project_readers WHERE login_role = SESSION_USER AND project_id = expected_project)
$$;
REVOKE ALL ON FUNCTION paperpilot_security.binding_matches(uuid) FROM PUBLIC;
-- Grant EXECUTE only to the project's restricted login after owner/ACL review.
