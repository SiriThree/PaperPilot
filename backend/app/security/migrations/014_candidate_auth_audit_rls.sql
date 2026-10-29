-- Isolated experiment only: 013 must already be installed. The operator must
-- provision the dedicated login separately; this migration creates no roles.
-- This permits completed session events in the same transaction as a session
-- change. Pre-session and anonymous rejection audit require a separate path.
CREATE POLICY auth_audit_completed_insert ON public.audit_events
    FOR INSERT TO paperpilot_auth_audit_writer
    WITH CHECK (
        project_id IS NULL
        AND resource_type = 'session'
        AND action IN ('auth.login', 'auth.logout', 'auth.session.invalid', 'auth.login.rate_limited')
        AND execution_status = 'completed'
        AND mock = false
    );

-- The older worker policy applies to PUBLIC. A restrictive policy also caps
-- this login even if an operator accidentally binds it to a project.
CREATE POLICY auth_audit_scope_cap ON public.audit_events
    AS RESTRICTIVE FOR INSERT TO paperpilot_auth_audit_writer
    WITH CHECK (
        project_id IS NULL
        AND resource_type = 'session'
        AND action IN ('auth.login', 'auth.logout', 'auth.session.invalid', 'auth.login.rate_limited')
        AND execution_status = 'completed'
        AND mock = false
    );
