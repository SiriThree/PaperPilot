-- Existing append-only events keep their original fingerprints and no details.
ALTER TABLE public.audit_events ADD COLUMN details jsonb
    CHECK (details IS NULL OR (jsonb_typeof(details) = 'object'
                              AND octet_length(details::text) <= 32768));

CREATE OR REPLACE VIEW paperpilot_audit.project_events WITH (security_barrier = true) AS
SELECT event_id, timestamp, request_id, actor_id, project_id, action, resource_type,
       decision, execution_status, policy_id, duration_ms, mock, details
FROM public.audit_events
WHERE project_id IS NOT NULL;
