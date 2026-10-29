-- Isolated worker-audit experiment ONLY, explicitly applied after 005 and 012.
-- The operator must independently create and audit this fixed NOLOGIN role,
-- make it the owner of event_fingerprint, and grant only the required SELECT.
-- This policy intentionally provides no system-level audit insert channel;
-- do not use this candidate as a production audit migration.
ALTER TABLE public.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_events FORCE ROW LEVEL SECURITY;

CREATE POLICY worker_audit_insert ON public.audit_events FOR INSERT TO PUBLIC
    WITH CHECK (project_id = paperpilot_business.bound_project()
                AND paperpilot_business.bound_identity(actor_id, project_id));

CREATE POLICY worker_audit_fingerprint_read ON public.audit_events FOR SELECT
    TO paperpilot_audit_worker_fingerprint_owner USING (true);
