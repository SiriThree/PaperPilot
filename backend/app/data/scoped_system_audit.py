"""Isolated-only, fail-closed candidate for pre-session and anonymous audit."""

import re
from pathlib import Path

from app.adapters.data_rules import fingerprint
from app.data.database import ConnectionConfig, PostgresDatabase
from app.errors import AppError

SYSTEM_ROLE = "paperpilot_system_audit_writer"
POLICY_EXPR = (
    "paperpilot_audit.system_event_allowed(project_id, action, resource_type, "
    "decision, execution_status, mock)"
)


def verify_system_policy(conn, trusted_owner):
    """Compare 015 to version-controlled source and fixed policy expectations."""
    source = (Path(__file__).parents[1] / "security/migrations/015_candidate_system_audit_rls.sql").read_text(
        encoding="utf-8-sig"
    )
    match = re.search(r"AS \$\$(.*?)\$\$;", source, re.S)
    item = conn.execute(
        "SELECT p.prosrc, pg_get_userbyid(p.proowner)=%s AS owner_ok, NOT p.prosecdef AS invoker, "
        "p.provolatile='i' AS immutable, p.proconfig=ARRAY['search_path=pg_catalog'] AS safe_path, "
        "pg_get_function_result(p.oid)='boolean' AS return_ok, "
        "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
        "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS no_public, "
        "has_function_privilege(%s,p.oid,'EXECUTE') AS callable "
        "FROM pg_proc p WHERE p.oid=to_regprocedure('paperpilot_audit.system_event_allowed(uuid,text,text,text,text,boolean)') "
        "AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')",
        (trusted_owner, SYSTEM_ROLE),
    ).fetchone()
    if (
        not match
        or not item
        or item["prosrc"] != match.group(1)
        or not all(
            item[k]
            for k in ("owner_ok", "invoker", "immutable", "safe_path", "return_ok", "no_public", "callable")
        )
    ):
        raise PermissionError("System audit function invalid")
    policies = conn.execute(
        "SELECT p.polname,p.polcmd,p.polpermissive,pg_get_expr(p.polwithcheck,p.polrelid) AS expr, "
        "pg_get_expr(p.polqual,p.polrelid) AS qual, "
        "p.polroles=ARRAY[(SELECT oid FROM pg_roles WHERE rolname=%s)]::oid[] AS role_ok "
        "FROM pg_policy p WHERE p.polrelid='public.audit_events'::regclass "
        "AND p.polname IN ('system_audit_insert','system_audit_scope_cap')",
        (SYSTEM_ROLE,),
    ).fetchall()
    policies = {p["polname"]: p for p in policies}
    if set(policies) != {"system_audit_insert", "system_audit_scope_cap"}:
        raise PermissionError("System audit policies missing")
    for name, permissive in (("system_audit_insert", True), ("system_audit_scope_cap", False)):
        p = policies[name]
        if not (
            p["polcmd"] == "a"
            and p["polpermissive"] == permissive
            and p["role_ok"]
            and p["qual"] is None
            and p["expr"] == POLICY_EXPR
        ):
            raise PermissionError("System audit policy invalid")


class ScopedSystemAuditWriter:
    """Candidate connection for the isolated test environment, never provision roles."""

    def __init__(self, config: ConnectionConfig, *, trusted_owner: str, trusted_binding_function_owner: str):
        if (
            not isinstance(config, ConnectionConfig)
            or config.user != SYSTEM_ROLE
            or config.host not in {"localhost", "127.0.0.1", "::1"}
            or not trusted_owner
            or trusted_owner == SYSTEM_ROLE
            or not trusted_binding_function_owner
            or trusted_binding_function_owner in {SYSTEM_ROLE, trusted_owner}
        ):
            raise ValueError("Invalid isolated system audit configuration")
        self.config = config
        self.trusted_owner = trusted_owner
        self.trusted_binding_function_owner = trusted_binding_function_owner
        self.database = PostgresDatabase(config, pool_size=1)
        self.database.set_transaction_guard(self._guard)

    def _guard(self, conn):
        try:
            self._verify(conn)
        except Exception:
            raise AppError(503, "AUDIT_UNAVAILABLE", "系统审计配置不可用") from None

    def _verify(self, conn):
        if conn.info.server_version // 10000 != 16:
            raise PermissionError("Unsupported database")
        row = conn.execute(
            "SELECT current_user=session_user AND current_user=%s AS identity_ok, "
            "rolcanlogin AND NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolinherit) AS flags_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) AS members_ok, "
            "NOT has_database_privilege(r.oid,current_database(),'CREATE,TEMP') AS db_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
            "AND has_schema_privilege(r.oid,n.oid,'CREATE')) AS schema_ok, "
            "has_table_privilege(r.oid,'public.audit_events','INSERT') AND "
            "NOT has_table_privilege(r.oid,'public.audit_events','SELECT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AND "
            "NOT has_any_column_privilege(r.oid,'public.audit_events','SELECT,UPDATE,REFERENCES') AS audit_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f') "
            "AND c.oid<>'public.audit_events'::regclass AND (has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            "OR has_any_column_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS other_tables_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
            "AND CASE WHEN c.relkind='S' THEN has_sequence_privilege(r.oid,c.oid,'USAGE,SELECT,UPDATE') ELSE false END) AS sequences_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND p.prosecdef "
            "AND p.oid NOT IN (to_regprocedure('paperpilot_business.bound_project()'), "
            "to_regprocedure('paperpilot_business.bound_identity(uuid,uuid)')) "
            "AND has_function_privilege(r.oid,p.oid,'EXECUTE')) AS functions_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='pg_catalog' "
            "AND p.proname=ANY(ARRAY['pg_read_file','pg_read_binary_file','pg_ls_dir','pg_stat_file']) "
            "AND has_function_privilege(r.oid,p.oid,'EXECUTE')) AS files_ok "
            "FROM pg_roles r WHERE r.rolname=current_user",
            (SYSTEM_ROLE,),
        ).fetchone()
        if not row or not all(row.values()):
            raise PermissionError("System audit identity invalid")
        table = conn.execute(
            "SELECT relrowsecurity AND relforcerowsecurity AND pg_get_userbyid(relowner)=%s AS ok "
            "FROM pg_class WHERE oid='public.audit_events'::regclass",
            (self.trusted_owner,),
        ).fetchone()
        if not table or not table["ok"]:
            raise PermissionError("System audit table invalid")
        owner = conn.execute(
            "SELECT NOT (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolinherit) AS flags_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) AS members_ok, "
            "NOT has_database_privilege(r.oid,current_database(),'CREATE,TEMP') AS db_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
            "AND has_schema_privilege(r.oid,n.oid,'CREATE')) AS schema_ok, "
            "has_table_privilege(r.oid,'paperpilot_business.project_logins','SELECT') AND "
            "NOT has_table_privilege(r.oid,'paperpilot_business.project_logins','INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AND "
            "NOT has_any_column_privilege(r.oid,'paperpilot_business.project_logins','INSERT,UPDATE,REFERENCES') AS binding_ok, "
            "has_column_privilege(r.oid,'public.users','user_id','SELECT') AND "
            "has_column_privilege(r.oid,'public.users','disabled','SELECT') AND "
            "NOT has_table_privilege(r.oid,'public.users','INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AND "
            "NOT has_any_column_privilege(r.oid,'public.users','INSERT,UPDATE,REFERENCES') AND "
            "NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid='public.users'::regclass AND a.attnum>0 "
            "AND NOT a.attisdropped AND a.attname NOT IN ('user_id','disabled') "
            "AND has_column_privilege(r.oid,a.attrelid,a.attnum,'SELECT')) AS users_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f') "
            "AND c.oid NOT IN ('paperpilot_business.project_logins'::regclass,'public.users'::regclass) "
            "AND (has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            "OR has_any_column_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS other_ok "
            "FROM pg_roles r WHERE r.rolname=%s",
            (self.trusted_binding_function_owner,),
        ).fetchone()
        if not owner or not all(owner.values()):
            raise PermissionError("System audit binding owner invalid")
        source = (
            Path(__file__).parents[1] / "security/migrations/012_candidate_business_active_identity.sql"
        ).read_text(encoding="utf-8-sig")
        for name, signature, result in (
            ("bound_project", "paperpilot_business.bound_project()", "uuid"),
            ("bound_identity", "paperpilot_business.bound_identity(uuid,uuid)", "boolean"),
        ):
            expected = re.search(
                r"CREATE OR REPLACE FUNCTION [^.]+\." + name + r"\([^;]*?AS \$\$(.*?)\$\$;", source, re.S
            )
            function = conn.execute(
                "SELECT p.prosrc, pg_get_userbyid(p.proowner)=%s AS owner_ok, p.prosecdef AS definer, "
                "p.provolatile='s' AS stable, p.proconfig=ARRAY['search_path=pg_catalog'] AS safe_path, "
                "pg_get_function_result(p.oid)=%s AS return_ok, has_function_privilege(current_user,p.oid,'EXECUTE') AS callable, "
                "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
                "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS no_public "
                "FROM pg_proc p WHERE p.oid=to_regprocedure(%s) AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')",
                (self.trusted_binding_function_owner, result, signature),
            ).fetchone()
            if (
                not expected
                or not function
                or function["prosrc"] != expected.group(1)
                or not all(
                    function[k]
                    for k in (
                        "owner_ok",
                        "definer",
                        "stable",
                        "safe_path",
                        "return_ok",
                        "callable",
                        "no_public",
                    )
                )
            ):
                raise PermissionError("System audit binding function invalid")
        verify_system_policy(conn, self.trusted_owner)

    def ready(self):
        with self.database.transaction():
            return True

    def append_audit(self, event):
        if event.project_id is not None or event.mock or event.timestamp.tzinfo is None:
            raise AppError(503, "AUDIT_UNAVAILABLE", "系统审计事件不符合策略")
        with self.database.transaction(write=True) as conn:
            conn.execute(
                "INSERT INTO public.audit_events(event_id,timestamp,request_id,actor_id,project_id,action,resource_type,"
                "decision,execution_status,policy_id,duration_ms,mock,event_fingerprint) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    event.event_id,
                    event.timestamp,
                    event.request_id,
                    event.actor_id,
                    event.project_id,
                    event.action,
                    event.resource_type,
                    event.decision,
                    event.execution_status,
                    event.policy_id,
                    event.duration_ms,
                    event.mock,
                    fingerprint(event),
                ),
            )

    def close(self):
        self.database.close()
