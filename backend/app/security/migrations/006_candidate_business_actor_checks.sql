-- Isolated candidate only. Apply explicitly after 005 in a disposable database.
-- Preserve project-wide reads but bind business insert authorship to SESSION_USER.
ALTER POLICY project_identity ON public.project_papers
    WITH CHECK (project_id = paperpilot_business.bound_project()
                AND paperpilot_business.bound_identity(imported_by, project_id));
ALTER POLICY project_identity ON public.tasks
    WITH CHECK (project_id = paperpilot_business.bound_project()
                AND paperpilot_business.bound_identity(actor_id, project_id));
ALTER POLICY project_identity ON public.idempotency_records
    WITH CHECK (project_id = paperpilot_business.bound_project()
                AND paperpilot_business.bound_identity(user_id, project_id));
ALTER POLICY project_identity ON public.outbox_events
    WITH CHECK (project_id = paperpilot_business.bound_project()
                AND paperpilot_business.bound_identity(actor_id, project_id));
