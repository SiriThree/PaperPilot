-- Explicit isolated candidate. Operator must assign a reviewed NOLOGIN owner,
-- grant only the documented column privileges and EXECUTE to the auth login.
-- This file is not automatically applied by the application.
CREATE SCHEMA IF NOT EXISTS paperpilot_auth;
REVOKE ALL ON SCHEMA paperpilot_auth FROM PUBLIC;
CREATE FUNCTION paperpilot_auth.lock_credential(requested_user uuid)
RETURNS TABLE(password_hash text, disabled boolean)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $$
    SELECT u.password_hash, u.disabled FROM public.users AS u
    WHERE u.user_id = requested_user FOR UPDATE
$$;
REVOKE ALL ON FUNCTION paperpilot_auth.lock_credential(uuid) FROM PUBLIC;
