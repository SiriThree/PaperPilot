-- Isolated candidate only. 013 and 014 must already be present. The operator
-- supplies paperpilot_system_audit_writer; this script does not create roles.
CREATE FUNCTION paperpilot_audit.system_event_allowed(
    project_id uuid, action text, resource_type text, decision text, execution_status text, mock boolean
) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
AS $$
    SELECT project_id IS NULL AND NOT mock AND (
        (action = 'auth.login' AND resource_type = 'session' AND
         ((decision = 'allowed' AND execution_status = 'pending') OR
          (decision = 'rejected' AND execution_status = 'completed'))) OR
        (action = 'auth.logout' AND resource_type = 'session' AND
         decision = 'allowed' AND execution_status = 'pending') OR
        (action = 'secure_query.access_denied' AND resource_type = 'secure_query' AND
         decision = 'rejected' AND execution_status = 'not_executed')
    )
$$;
REVOKE ALL ON FUNCTION paperpilot_audit.system_event_allowed(uuid,text,text,text,text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION paperpilot_audit.system_event_allowed(uuid,text,text,text,text,boolean)
    TO paperpilot_system_audit_writer;

CREATE POLICY system_audit_insert ON public.audit_events
    FOR INSERT TO paperpilot_system_audit_writer
    WITH CHECK (paperpilot_audit.system_event_allowed(project_id, action, resource_type, decision, execution_status, mock));
CREATE POLICY system_audit_scope_cap ON public.audit_events
    AS RESTRICTIVE FOR INSERT TO paperpilot_system_audit_writer
    WITH CHECK (paperpilot_audit.system_event_allowed(project_id, action, resource_type, decision, execution_status, mock));
