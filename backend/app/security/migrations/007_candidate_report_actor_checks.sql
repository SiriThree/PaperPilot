-- Isolated candidate only. Apply explicitly after 005 and 006.
-- Project members may edit existing reports; inserted author must be the login.
CREATE POLICY report_insert_actor ON public.reports AS RESTRICTIVE FOR INSERT
    WITH CHECK (paperpilot_business.bound_identity(created_by, project_id));
CREATE POLICY report_insert_actor ON public.report_versions AS RESTRICTIVE FOR INSERT
    WITH CHECK (paperpilot_business.bound_identity(created_by, project_id));
