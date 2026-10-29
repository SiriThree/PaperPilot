-- Scope ordinary audit listing to project events. Project membership must still
-- be checked by the trusted application before querying this view: a shared
-- PostgreSQL login is not intrinsically bound to a particular project.
-- The migration operator must independently provision a NOLOGIN, non-superuser
-- owner and grant it only the required audit_events SELECT privilege before
-- granting this view/function to the application login. No grants to app users
-- are made here; production deployment remains subject to security review.
CREATE SCHEMA paperpilot_audit;
REVOKE ALL ON SCHEMA paperpilot_audit FROM PUBLIC;

CREATE VIEW paperpilot_audit.project_events WITH (security_barrier = true) AS
SELECT event_id, timestamp, request_id, actor_id, project_id, action, resource_type,
       decision, execution_status, policy_id, duration_ms, mock
FROM public.audit_events
WHERE project_id IS NOT NULL;
REVOKE ALL ON paperpilot_audit.project_events FROM PUBLIC;

-- Idempotency comparison needs only the fingerprint for an event ID, including
-- system events. Do not expose the remainder of system audit rows to this role.
CREATE FUNCTION paperpilot_audit.event_fingerprint(requested_event uuid)
RETURNS varchar LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT event_fingerprint FROM public.audit_events WHERE event_id = requested_event
$$;
REVOKE ALL ON FUNCTION paperpilot_audit.event_fingerprint(uuid) FROM PUBLIC;
