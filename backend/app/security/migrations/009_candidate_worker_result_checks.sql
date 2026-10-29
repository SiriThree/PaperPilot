-- Isolated candidate only. Apply after 005, 006 and 008.
-- Invoker functions use the caller's RLS; no privileged owner is assumed.
CREATE FUNCTION paperpilot_business.worker_has_lease(wanted_task uuid, wanted_project uuid, wanted_attempt integer)
RETURNS boolean LANGUAGE sql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.tasks t JOIN public.task_attempts a
          ON a.task_id=t.task_id AND a.project_id=t.project_id AND a.attempt_no=t.attempts
        WHERE t.task_id=wanted_task AND t.project_id=wanted_project
          AND paperpilot_business.bound_identity(t.actor_id,t.project_id)
          AND t.status='processing' AND t.attempts=wanted_attempt
          AND a.status='processing' AND a.lease_expires_at>clock_timestamp()
    )
$$;
CREATE FUNCTION paperpilot_business.worker_has_result(wanted_task uuid, wanted_project uuid)
RETURNS boolean LANGUAGE sql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.task_results r
        WHERE r.task_id=wanted_task AND r.project_id=wanted_project
          AND paperpilot_business.worker_has_lease(r.task_id,r.project_id,r.committed_attempt)
    )
$$;
CREATE FUNCTION paperpilot_business.worker_has_process(wanted_paper uuid, wanted_project uuid)
RETURNS boolean LANGUAGE sql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.tasks t JOIN public.task_papers p
          ON p.task_id=t.task_id AND p.project_id=t.project_id
        WHERE t.project_id=wanted_project AND p.paper_id=wanted_paper AND t.kind='process'
          AND paperpilot_business.worker_has_lease(t.task_id,t.project_id,t.attempts)
    )
$$;
REVOKE ALL ON FUNCTION paperpilot_business.worker_has_lease(uuid,uuid,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION paperpilot_business.worker_has_result(uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION paperpilot_business.worker_has_process(uuid,uuid) FROM PUBLIC;
CREATE POLICY worker_result_actor ON public.task_results AS RESTRICTIVE FOR INSERT
    WITH CHECK (paperpilot_business.worker_has_lease(task_id,project_id,committed_attempt));
CREATE POLICY worker_result_actor ON public.evidence_fields AS RESTRICTIVE FOR INSERT
    WITH CHECK (paperpilot_business.worker_has_result(task_id,project_id));
CREATE POLICY worker_result_actor ON public.result_sources AS RESTRICTIVE FOR INSERT
    WITH CHECK (paperpilot_business.worker_has_result(task_id,project_id));
CREATE POLICY worker_result_actor ON public.chunks AS RESTRICTIVE FOR INSERT
    WITH CHECK (paperpilot_business.worker_has_process(paper_id,project_id));
