"""Isolated-only preflight for the 014 transactional authentication audit."""

import re
from pathlib import Path

from app.data.scoped_worker_audit import AUTH_AUDIT_ROLE, AUTH_INSERT_CHECK
from app.errors import AppError


class CandidateAuthAuditGuard:
    """Additional audit guard; not a replacement for AuthenticationBoundary."""

    def __init__(self, *, trusted_audit_owner, trusted_binding_owner):
        if (
            not trusted_audit_owner
            or not trusted_binding_owner
            or len({trusted_audit_owner, trusted_binding_owner, AUTH_AUDIT_ROLE}) != 3
        ):
            raise ValueError("Trusted, distinct owner identities required")
        self.trusted_audit_owner = trusted_audit_owner
        self.trusted_binding_owner = trusted_binding_owner

    def __call__(self, conn):
        try:
            self.verify(conn)
        except Exception:
            raise AppError(503, "AUDIT_UNAVAILABLE", "认证审计配置不可用") from None

    def verify(self, conn):
        if conn.info.server_version // 10000 != 16:
            raise PermissionError("Unsupported database")
        identity = conn.execute(
            "SELECT current_user=session_user AND current_user=%s AS identity_ok, "
            "rolcanlogin AND NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolinherit) AS flags_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) AS members_ok, "
            "NOT has_database_privilege(r.oid,current_database(),'CREATE,TEMP') AS db_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
            "AND has_schema_privilege(r.oid,n.oid,'CREATE')) AS schemas_ok, "
            "has_table_privilege(r.oid,'public.audit_events','INSERT') AND "
            "NOT has_table_privilege(r.oid,'public.audit_events','SELECT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AND "
            "NOT has_any_column_privilege(r.oid,'public.audit_events','SELECT,UPDATE,REFERENCES') AS audit_ok "
            "FROM pg_roles r WHERE r.rolname=current_user",
            (AUTH_AUDIT_ROLE,),
        ).fetchone()
        table = conn.execute(
            "SELECT relrowsecurity AND relforcerowsecurity AND pg_get_userbyid(relowner)=%s AS ok "
            "FROM pg_class WHERE oid='public.audit_events'::regclass",
            (self.trusted_audit_owner,),
        ).fetchone()
        if not identity or not all(identity.values()) or not table or not table["ok"]:
            raise PermissionError("Authentication audit identity invalid")
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
                "pg_get_function_result(p.oid)=%s AS result_ok, has_function_privilege(current_user,p.oid,'EXECUTE') AS callable, "
                "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
                "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS no_public FROM pg_proc p "
                "WHERE p.oid=to_regprocedure(%s) AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')",
                (self.trusted_binding_owner, result, signature),
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
                        "result_ok",
                        "callable",
                        "no_public",
                    )
                )
            ):
                raise PermissionError("Authentication binding function invalid")
        if (
            conn.execute("SELECT paperpilot_business.bound_project() IS NULL AS unbound").fetchone()[
                "unbound"
            ]
            is not True
        ):
            raise PermissionError("Authentication login must not be project bound")
        policies = conn.execute(
            "SELECT polname,polcmd,polpermissive,pg_get_expr(polwithcheck,polrelid) AS expr, "
            "pg_get_expr(polqual,polrelid) AS qual, "
            "polroles=ARRAY[(SELECT oid FROM pg_roles WHERE rolname=%s)]::oid[] AS role_ok "
            "FROM pg_policy WHERE polrelid='public.audit_events'::regclass AND "
            "polname IN ('auth_audit_completed_insert','auth_audit_scope_cap')",
            (AUTH_AUDIT_ROLE,),
        ).fetchall()
        policies = {row["polname"]: row for row in policies}
        if set(policies) != {"auth_audit_completed_insert", "auth_audit_scope_cap"}:
            raise PermissionError("Authentication audit policies missing")
        for name, permissive in (("auth_audit_completed_insert", True), ("auth_audit_scope_cap", False)):
            row = policies[name]
            if not (
                row["polcmd"] == "a"
                and row["polpermissive"] == permissive
                and row["role_ok"]
                and row["qual"] is None
                and row["expr"] == AUTH_INSERT_CHECK
            ):
                raise PermissionError("Authentication audit policy invalid")
