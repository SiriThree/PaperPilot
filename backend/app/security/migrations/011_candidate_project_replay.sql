-- Isolated candidate only; apply explicitly after 010. This key is separate
-- from request_id and is not part of the frozen DataPort contract.
CREATE TABLE paperpilot_business.bootstrap_requests (
    user_id uuid NOT NULL REFERENCES public.users(user_id),
    operation_key varchar(128) NOT NULL CHECK (operation_key ~ '^[A-Za-z0-9_-]{1,128}$'),
    project_id uuid NOT NULL REFERENCES public.projects(project_id) DEFERRABLE INITIALLY DEFERRED,
    name varchar(100) NOT NULL,
    description varchar(1000) NOT NULL,
    PRIMARY KEY(user_id, operation_key)
);
REVOKE ALL ON paperpilot_business.bootstrap_requests FROM PUBLIC;

CREATE FUNCTION paperpilot_business.bootstrap_create_replay(
    wanted_name text, wanted_description text, wanted_request uuid, wanted_key text
) RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    actor uuid := paperpilot_business.bootstrap_user();
    project uuid := pg_catalog.gen_random_uuid();
    inserted uuid;
    previous record;
BEGIN
    IF actor IS NULL OR wanted_request IS NULL OR wanted_key IS NULL
       OR wanted_key !~ '^[A-Za-z0-9_-]{1,128}$'
       OR wanted_name IS NULL OR wanted_description IS NULL
       OR length(wanted_name) NOT BETWEEN 1 AND 100 OR length(wanted_description)>1000 THEN
        RAISE EXCEPTION 'project creation rejected' USING ERRCODE='42501';
    END IF;
    INSERT INTO paperpilot_business.bootstrap_requests(user_id,operation_key,project_id,name,description)
        VALUES(actor,wanted_key,project,wanted_name,wanted_description)
        ON CONFLICT(user_id,operation_key) DO NOTHING RETURNING project_id INTO inserted;
    IF inserted IS NULL THEN
        SELECT r.project_id,r.name,r.description INTO previous
          FROM paperpilot_business.bootstrap_requests r WHERE r.user_id=actor AND r.operation_key=wanted_key;
        IF NOT FOUND OR previous.name IS DISTINCT FROM wanted_name
           OR previous.description IS DISTINCT FROM wanted_description THEN
            RAISE EXCEPTION 'idempotency conflict' USING ERRCODE='23505';
        END IF;
        RETURN previous.project_id;
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
REVOKE ALL ON FUNCTION paperpilot_business.bootstrap_create_replay(text,text,uuid,text) FROM PUBLIC;
