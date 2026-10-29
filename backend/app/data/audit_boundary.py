"""Explicit PostgreSQL 16 audit-identity preflight; never provisions privileges.

This is a necessary deployment check, not proof of project isolation: the
business identity is shared and project authorization remains in the service.
Run it using the proposed business login and an independently trusted owner name.
"""

import hashlib

from psycopg.rows import tuple_row

_VIEW_SHA256 = "24fe693b5ae09a11fb4f157f28eb87dc648bd700b0fd173015efaa3fabedb287"
_FUNCTION_BODY = "\n    SELECT event_fingerprint FROM public.audit_events WHERE event_id = requested_event\n"


def verify_audit_boundary(connection, *, trusted_owner: str, scoped_read: bool = False) -> None:
    """Reject unsafe or unknown ownership/grants. This function makes no writes."""
    if not isinstance(trusted_owner, str) or not trusted_owner.strip():
        raise ValueError("An independently trusted audit owner is required")
    if connection.info.server_version // 10000 != 16:
        raise PermissionError("Unsupported audit security baseline PostgreSQL version")
    with connection.transaction():
        with connection.cursor(row_factory=tuple_row) as cur:
            cur.execute("SET LOCAL search_path=pg_catalog")
            cur.execute(
                "SELECT current_user=session_user, NOT (rolsuper OR rolbypassrls OR rolcreatedb "
                "OR rolcreaterole OR rolreplication OR rolinherit), "
                "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid), "
                "NOT has_database_privilege(current_user,current_database(),'CREATE,TEMP'), "
                "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' "
                "AND n.nspname<>'information_schema' AND has_schema_privilege(current_user,n.oid,'CREATE')) "
                "FROM pg_roles r WHERE rolname=current_user"
            )
            role = cur.fetchone()
            if not role or not all(role):
                raise PermissionError("Audit business identity verification failed")
            cur.execute(
                "SELECT has_table_privilege(current_user,c.oid,'INSERT'), "
                "NOT has_table_privilege(current_user,c.oid,'SELECT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'), "
                "NOT has_any_column_privilege(current_user,c.oid,'SELECT,UPDATE,REFERENCES'), "
                "pg_get_userbyid(c.relowner)<>current_user FROM pg_class c "
                "WHERE c.oid=to_regclass('public.audit_events') AND c.relkind='r'"
            )
            base = cur.fetchone()
            if not base or not all(base):
                raise PermissionError("Audit base table access verification failed")
            cur.execute(
                "SELECT NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole "
                "OR rolreplication OR rolcanlogin OR rolinherit), "
                "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid), "
                "NOT has_database_privilege(r.oid,current_database(),'CREATE,TEMP'), "
                "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' "
                "AND n.nspname<>'information_schema' AND has_schema_privilege(r.oid,n.oid,'CREATE')), "
                "has_table_privilege(r.oid,'public.audit_events','SELECT'), "
                "NOT has_table_privilege(r.oid,'public.audit_events','INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'), "
                "NOT has_any_column_privilege(r.oid,'public.audit_events','INSERT,UPDATE,REFERENCES') "
                "FROM pg_roles r WHERE rolname=%s",
                (trusted_owner,),
            )
            owner = cur.fetchone()
            if not owner or not all(owner):
                raise PermissionError("Audit owner verification failed")
            cur.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
                "AND c.relkind IN ('r','p','v','m','f') AND c.oid NOT IN "
                "(to_regclass('public.audit_events'),to_regclass('paperpilot_audit.project_events')) "
                "AND (has_table_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                "OR has_any_column_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) ",
                (trusted_owner, trusted_owner),
            )
            if not cur.fetchone()[0]:
                raise PermissionError("Audit owner has unexpected object privileges")
            # SECURITY DEFINER functions may bypass the table ACLs above. Only the
            # reviewed fingerprint function is part of the audit boundary.
            cur.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND p.prosecdef "
                "AND p.oid<>COALESCE(to_regprocedure('paperpilot_audit.event_fingerprint(uuid)')::oid,0::oid) "
                "AND (has_function_privilege(current_user,p.oid,'EXECUTE') "
                "OR has_function_privilege(%s,p.oid,'EXECUTE'))) ",
                (trusted_owner,),
            )
            if not cur.fetchone()[0]:
                raise PermissionError("Unexpected security definer function privileges")
            cur.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='pg_catalog' AND p.proname=ANY(%s) "
                "AND (has_function_privilege(current_user,p.oid,'EXECUTE') "
                "OR has_function_privilege(%s,p.oid,'EXECUTE'))) ",
                (
                    [
                        "pg_read_file",
                        "pg_read_binary_file",
                        "pg_ls_dir",
                        "pg_stat_file",
                        "pg_ls_logdir",
                        "pg_ls_waldir",
                        "pg_ls_archive_statusdir",
                    ],
                    trusted_owner,
                ),
            )
            if not cur.fetchone()[0]:
                raise PermissionError("Unexpected server file function privileges")
            cur.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
                "AND CASE WHEN c.relkind='S' THEN "
                "has_sequence_privilege(%s,c.oid,'USAGE,SELECT,UPDATE') ELSE false END)",
                (trusted_owner,),
            )
            if not cur.fetchone()[0]:
                raise PermissionError("Audit owner has unexpected sequence privileges")
            cur.execute(
                "SELECT pg_get_userbyid(c.relowner), "
                "'security_barrier=true'=ANY(COALESCE(c.reloptions,ARRAY[]::text[])), "
                "NOT ('security_invoker=true'=ANY(COALESCE(c.reloptions,ARRAY[]::text[]))), "
                "has_table_privilege(current_user,c.oid,'SELECT'), pg_get_viewdef(c.oid,false), "
                "NOT has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'), "
                "NOT has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES') "
                "FROM pg_class c WHERE c.oid=to_regclass('paperpilot_audit.project_events') AND c.relkind='v'"
            )
            view = cur.fetchone()
            if (
                not view
                or view[0] != trusted_owner
                or not all(view[1:3])
                or view[3] is scoped_read
                or hashlib.sha256(view[4].encode("utf-8")).hexdigest() != _VIEW_SHA256
                or not all(view[5:])
            ):
                raise PermissionError("Audit view verification failed")
            cur.execute(
                "SELECT pg_get_userbyid(p.proowner),p.prosecdef,p.provolatile='s', "
                "p.proconfig=ARRAY['search_path=pg_catalog'],p.prosrc, "
                "has_function_privilege(current_user,p.oid,'EXECUTE'), "
                "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
                "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') "
                "FROM pg_proc p WHERE p.oid=to_regprocedure('paperpilot_audit.event_fingerprint(uuid)') "
                "AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql') "
                "AND p.prorettype='character varying'::regtype"
            )
            function = cur.fetchone()
            if (
                not function
                or function[0] != trusted_owner
                or not all(function[1:4])
                or function[4] != _FUNCTION_BODY
                or not all(function[5:])
            ):
                raise PermissionError("Audit replay function verification failed")
