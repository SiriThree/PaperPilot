-- Isolated candidate only. The NOLOGIN role must be provisioned separately.
-- Apply after 005, in a disposable database, as an explicit operator action.
CREATE TABLE paperpilot_business.bootstrap_logins (
    login_role text PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE
);
REVOKE ALL ON paperpilot_business.bootstrap_logins FROM PUBLIC;

CREATE FUNCTION paperpilot_business.bootstrap_user() RETURNS uuid
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $$
    SELECT b.user_id FROM paperpilot_business.bootstrap_logins b
    JOIN public.users u ON u.user_id=b.user_id
    WHERE b.login_role=SESSION_USER AND NOT u.disabled
$$;
REVOKE ALL ON FUNCTION paperpilot_business.bootstrap_user() FROM PUBLIC;

CREATE POLICY bootstrap_project_insert ON public.projects FOR INSERT TO paperpilot_bootstrap_executor
    WITH CHECK (owner_id=paperpilot_business.bootstrap_user() AND owner_role='owner');
CREATE POLICY bootstrap_member_insert ON public.project_members FOR INSERT TO paperpilot_bootstrap_executor
    WITH CHECK (user_id=paperpilot_business.bootstrap_user() AND role='owner');
CREATE POLICY bootstrap_outbox_insert ON public.outbox_events FOR INSERT TO paperpilot_bootstrap_executor
    WITH CHECK (actor_id=paperpilot_business.bootstrap_user());

CREATE FUNCTION paperpilot_business.bootstrap_matches(wanted_user uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT paperpilot_business.bootstrap_user()=wanted_user
$$;
REVOKE ALL ON FUNCTION paperpilot_business.bootstrap_matches(uuid) FROM PUBLIC;

CREATE FUNCTION paperpilot_business.bootstrap_create(wanted_name text, wanted_description text, wanted_request uuid)
RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    actor uuid := paperpilot_business.bootstrap_user();
    project uuid := pg_catalog.gen_random_uuid();
BEGIN
    IF actor IS NULL OR wanted_request IS NULL OR wanted_name IS NULL OR wanted_description IS NULL
       OR length(wanted_name) NOT BETWEEN 1 AND 100 OR length(wanted_description)>1000 THEN
        RAISE EXCEPTION 'project creation rejected' USING ERRCODE='42501';
    END IF;
    INSERT INTO public.projects(project_id,name,description,owner_id,created_at)
        VALUES(project,wanted_name,wanted_description,actor,now());
    INSERT INTO public.project_members(project_id,user_id,role,joined_at)
        VALUES(project,actor,'owner',now());
    INSERT INTO public.outbox_events(event_id,project_id,actor_id,request_id,payload)
        VALUES(pg_catalog.gen_random_uuid(),project,actor,wanted_request,
            jsonb_build_object('action','create_project','resource_type','project',
                'resource_id',project::text,'decision','allowed','execution_status','succeeded',
                'duration_ms',0,'mock',false));
    RETURN project;
END
$$;
REVOKE ALL ON FUNCTION paperpilot_business.bootstrap_create(text,text,uuid) FROM PUBLIC;
