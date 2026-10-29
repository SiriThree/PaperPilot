-- Isolated candidate only. Apply after 005 and 006; not a production migration.
ALTER POLICY project_identity ON public.task_attempts
    WITH CHECK (project_id = paperpilot_business.bound_project()
                AND paperpilot_business.bound_identity(actor_id, project_id));
