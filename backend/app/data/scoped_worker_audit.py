"""Readiness-checked project audit writer for the isolated 013 candidate only."""

from pathlib import Path
import re

from app.data.adapter import PostgresData
from app.data.database import PostgresDatabase
from app.data.scoped_business import ScopedBusinessConfig
from app.data.settings import DataSettings
from app.errors import AppError

FINGERPRINT_OWNER = "paperpilot_audit_worker_fingerprint_owner"
AUTH_AUDIT_ROLE = "paperpilot_auth_audit_writer"
INSERT_CHECK = "((project_id = paperpilot_business.bound_project()) AND paperpilot_business.bound_identity(actor_id, project_id))"
AUTH_INSERT_CHECK = (
    "((project_id IS NULL) AND (resource_type = 'session'::text) AND "
    "(action = ANY (ARRAY['auth.login'::text, 'auth.logout'::text, 'auth.session.invalid'::text, "
    "'auth.login.rate_limited'::text])) AND (execution_status = 'completed'::text) AND (mock = false))"
)


class ScopedWorkerAuditWriter:
    """Bound audit writes using a separate identity; never provisions database objects."""

    def __init__(self, config: ScopedBusinessConfig):
        if (
            not isinstance(config, ScopedBusinessConfig)
            or config.connection.host not in {"localhost", "127.0.0.1", "::1"}
            or not config.function_owner
            or not config.binding_owner
            or config.connection.user in {config.function_owner, config.binding_owner, FINGERPRINT_OWNER}
        ):
            raise ValueError("Invalid isolated worker audit configuration")
        self.config = config
        self.database = PostgresDatabase(config.connection, pool_size=1)
        self.database.set_transaction_guard(self._verify)
        self._data = PostgresData(self.database, DataSettings())

    def _verify(self, conn):
        try:
            self._verify_database(conn)
        except Exception:
            raise AppError(503, "AUDIT_UNAVAILABLE", "审计隔离配置不可用") from None

    def _verify_database(self, conn):
        cfg = self.config
        if conn.info.server_version // 10000 != 16:
            raise PermissionError("Unsupported audit baseline")
        for name, login in (
            (cfg.connection.user, True),
            (cfg.function_owner, False),
            (FINGERPRINT_OWNER, False),
        ):
            role = conn.execute(
                "SELECT rolcanlogin=%s AS login_ok, NOT (rolsuper OR rolbypassrls OR rolcreatedb OR "
                "rolcreaterole OR rolreplication OR rolinherit) AS flags_ok, "
                "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) AS members_ok, "
                "NOT has_database_privilege(r.oid,current_database(),'CREATE,TEMP') AS db_ok, "
                "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' AND "
                "n.nspname<>'information_schema' AND has_schema_privilege(r.oid,n.oid,'CREATE')) AS schema_ok "
                "FROM pg_roles r WHERE r.rolname=%s",
                (login, name),
            ).fetchone()
            if not role or not all(role.values()):
                raise PermissionError("Unsafe audit identity")
        if len({cfg.connection.user, cfg.function_owner, cfg.binding_owner, FINGERPRINT_OWNER}) != 4:
            raise PermissionError("Audit identities must differ")
        for name in (cfg.connection.user, cfg.function_owner, FINGERPRINT_OWNER):
            extra = conn.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
                "AND CASE WHEN c.relkind='S' THEN has_sequence_privilege(%s,c.oid,'USAGE,SELECT,UPDATE') ELSE false END) AS sequences_ok, "
                "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='pg_catalog' AND p.proname=ANY(ARRAY['pg_read_file','pg_read_binary_file', "
                "'pg_ls_dir','pg_stat_file','pg_ls_logdir','pg_ls_waldir','pg_ls_archive_statusdir']) "
                "AND has_function_privilege(%s,p.oid,'EXECUTE')) AS files_ok, "
                "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND p.prosecdef "
                "AND p.oid NOT IN (to_regprocedure('paperpilot_business.bound_project()'), "
                "to_regprocedure('paperpilot_business.bound_identity(uuid,uuid)'), "
                "to_regprocedure('paperpilot_audit.event_fingerprint(uuid)')) "
                "AND has_function_privilege(%s,p.oid,'EXECUTE')) AS functions_ok",
                (name, name, name),
            ).fetchone()
            if not extra or not all(extra.values()):
                raise PermissionError(f"Unsafe audit function or sequence privileges: {extra}")
        caller = conn.execute(
            "SELECT current_user=session_user AND current_user=%s AS identity_ok, "
            "has_table_privilege(current_user,'public.audit_events','INSERT') AS insert_ok, "
            "NOT has_table_privilege(current_user,'public.audit_events','SELECT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS table_ok, "
            "NOT has_any_column_privilege(current_user,'public.audit_events','SELECT,UPDATE,REFERENCES') AS columns_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f') "
            "AND c.oid NOT IN ('public.audit_events'::regclass,'public.projects'::regclass,'public.users'::regclass) "
            "AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            "OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS other_relations_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
            "AND CASE WHEN c.relkind='S' THEN has_sequence_privilege(current_user,c.oid,'USAGE,SELECT,UPDATE') ELSE false END) AS sequences_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND p.prosecdef "
            "AND p.oid NOT IN (to_regprocedure('paperpilot_business.bound_project()'), "
            "to_regprocedure('paperpilot_business.bound_identity(uuid,uuid)'), "
            "to_regprocedure('paperpilot_audit.event_fingerprint(uuid)')) "
            "AND has_function_privilege(current_user,p.oid,'EXECUTE')) AS functions_ok",
            (cfg.connection.user,),
        ).fetchone()
        if not caller or not all(caller.values()):
            raise PermissionError("Unsafe audit caller grants")
        for table, expected in (("users", {"user_id"}), ("projects", {"project_id"})):
            columns = conn.execute(
                "SELECT a.attname,has_column_privilege(current_user,a.attrelid,a.attnum,'SELECT') AS allowed "
                "FROM pg_attribute a WHERE a.attrelid=to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped",
                ("public." + table,),
            ).fetchall()
            if {row["attname"] for row in columns if row["allowed"]} != expected:
                raise PermissionError("Audit caller columns invalid")
            if conn.execute(
                "SELECT has_table_privilege(current_user,to_regclass(%s),'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                "OR has_any_column_privilege(current_user,to_regclass(%s),'INSERT,UPDATE,REFERENCES') AS bad",
                ("public." + table, "public." + table),
            ).fetchone()["bad"]:
                raise PermissionError("Audit caller write grants invalid")
        table = conn.execute(
            "SELECT c.relrowsecurity,c.relforcerowsecurity,pg_get_userbyid(c.relowner)=%s AS owner_ok "
            "FROM pg_class c WHERE c.oid=to_regclass('public.audit_events') AND c.relkind='r'",
            (cfg.binding_owner,),
        ).fetchone()
        if not table or not all(table.values()):
            raise PermissionError("Audit RLS missing or owner invalid")
        policies = conn.execute(
            "SELECT p.polname,p.polcmd,p.polpermissive,p.polroles,pg_get_expr(p.polqual,p.polrelid) AS qual, "
            "pg_get_expr(p.polwithcheck,p.polrelid) AS check_expr, "
            "p.polroles=ARRAY[(SELECT oid FROM pg_roles WHERE rolname=%s)]::oid[] AS owner_role, "
            "p.polroles=ARRAY[(SELECT oid FROM pg_roles WHERE rolname=%s)]::oid[] AS auth_role "
            "FROM pg_policy p WHERE p.polrelid='public.audit_events'::regclass",
            (FINGERPRINT_OWNER, AUTH_AUDIT_ROLE),
        ).fetchall()
        by_name = {row["polname"]: row for row in policies}
        required = {"worker_audit_insert", "worker_audit_fingerprint_read"}
        auth_policies = {"auth_audit_completed_insert", "auth_audit_scope_cap"}
        system_policies = {"system_audit_insert", "system_audit_scope_cap"}
        if set(by_name) not in (
            required,
            required | auth_policies,
            required | auth_policies | system_policies,
        ):
            raise PermissionError("Unexpected audit RLS policies")
        if system_policies <= set(by_name):
            from app.data.scoped_system_audit import SYSTEM_ROLE, verify_system_policy

            role = conn.execute(
                "SELECT rolcanlogin AND NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR "
                "rolreplication OR rolinherit) AS role_ok, NOT EXISTS (SELECT 1 FROM pg_auth_members "
                "WHERE member=r.oid OR roleid=r.oid) AS members_ok FROM pg_roles r WHERE r.rolname=%s",
                (SYSTEM_ROLE,),
            ).fetchone()
            if (
                not role
                or not all(role.values())
                or SYSTEM_ROLE
                in {
                    cfg.connection.user,
                    cfg.function_owner,
                    cfg.binding_owner,
                    FINGERPRINT_OWNER,
                    AUTH_AUDIT_ROLE,
                }
            ):
                raise PermissionError("System audit identity invalid")
            verify_system_policy(conn, cfg.binding_owner)
        if "auth_audit_completed_insert" in by_name:
            auth = by_name["auth_audit_completed_insert"]
            cap = by_name["auth_audit_scope_cap"]
            role = conn.execute(
                "SELECT rolcanlogin AND NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR "
                "rolreplication OR rolinherit) AS role_ok, NOT EXISTS (SELECT 1 FROM pg_auth_members "
                "WHERE member=r.oid OR roleid=r.oid) AS members_ok "
                "FROM pg_roles r WHERE r.rolname=%s",
                (AUTH_AUDIT_ROLE,),
            ).fetchone()
            if not (
                auth["polcmd"] == "a"
                and auth["polpermissive"]
                and auth["auth_role"]
                and auth["qual"] is None
                and auth["check_expr"] == AUTH_INSERT_CHECK
                and cap["polcmd"] == "a"
                and not cap["polpermissive"]
                and cap["auth_role"]
                and cap["qual"] is None
                and cap["check_expr"] == AUTH_INSERT_CHECK
                and role
                and all(role.values())
                and AUTH_AUDIT_ROLE
                not in {cfg.connection.user, cfg.function_owner, cfg.binding_owner, FINGERPRINT_OWNER}
            ):
                raise PermissionError("Authentication audit RLS policy invalid")
        insert, read = by_name["worker_audit_insert"], by_name["worker_audit_fingerprint_read"]
        if not (
            insert["polcmd"] == "a"
            and insert["polpermissive"]
            and insert["polroles"] == [0]
            and insert["qual"] is None
            and insert["check_expr"] == INSERT_CHECK
            and read["polcmd"] == "r"
            and read["polpermissive"]
            and read["owner_role"]
            and read["qual"] == "true"
            and read["check_expr"] is None
        ):
            raise PermissionError("Audit RLS policy invalid")
        self._verify_owners_and_binding(conn)
        self._verify_functions(conn)
        scope = conn.execute(
            "SELECT paperpilot_business.bound_identity(%s,%s) AS bound", (cfg.user_id, cfg.project_id)
        ).fetchone()
        if not scope or not scope["bound"]:
            raise PermissionError("Audit scope invalid or disabled")

    def _verify_owners_and_binding(self, conn):
        cfg = self.config
        owner = conn.execute(
            "SELECT has_table_privilege(%s,'public.audit_events','SELECT') AS read_ok, "
            "NOT has_table_privilege(%s,'public.audit_events','INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS no_write, "
            "NOT has_any_column_privilege(%s,'public.audit_events','INSERT,UPDATE,REFERENCES') AS no_column_write, "
            "NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f') "
            "AND c.oid<>'public.audit_events'::regclass AND (has_table_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            "OR has_any_column_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS no_other ",
            (FINGERPRINT_OWNER,) * 5,
        ).fetchone()
        if not owner or not all(owner.values()):
            raise PermissionError("Fingerprint owner grants invalid")
        function_owner = conn.execute(
            "SELECT has_table_privilege(%s,'paperpilot_business.project_logins','SELECT') AS binding_read, "
            "NOT has_table_privilege(%s,'paperpilot_business.project_logins','INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS binding_readonly, "
            "NOT has_any_column_privilege(%s,'paperpilot_business.project_logins','INSERT,UPDATE,REFERENCES') AS binding_columns_readonly, "
            "NOT has_table_privilege(%s,'public.users','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS users_no_table, "
            "NOT has_any_column_privilege(%s,'public.users','INSERT,UPDATE,REFERENCES') AS users_no_write, "
            "has_column_privilege(%s,'public.users','user_id','SELECT') AND has_column_privilege(%s,'public.users','disabled','SELECT') "
            "AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid='public.users'::regclass AND a.attnum>0 AND NOT a.attisdropped "
            "AND a.attname NOT IN ('user_id','disabled') AND has_column_privilege(%s,a.attrelid,a.attnum,'SELECT')) AS users_columns_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE left(n.nspname,3)<>'pg_' "
            "AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f') "
            "AND c.oid NOT IN ('paperpilot_business.project_logins'::regclass,'public.users'::regclass) "
            "AND (has_table_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            "OR has_any_column_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS no_other",
            (cfg.function_owner,) * 10,
        ).fetchone()
        if not function_owner or not all(function_owner.values()):
            raise PermissionError("Binding function owner grants invalid")
        binding = conn.execute(
            "SELECT pg_get_userbyid(c.relowner)=%s AS owner_ok, "
            "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a "
            "WHERE a.grantee<>c.relowner AND a.privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER')) AS no_writers, "
            "NOT EXISTS (SELECT 1 FROM pg_attribute a, LATERAL aclexplode(a.attacl) x "
            "WHERE a.attrelid=c.oid AND x.grantee<>c.relowner AND x.privilege_type IN ('INSERT','UPDATE','REFERENCES')) AS no_column_writers, "
            "EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='p' "
            "AND k.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='login_role' AND attnotnull)]::smallint[]) AS unique_login, "
            "EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='f' AND k.convalidated AND k.confdeltype='c' "
            "AND k.confrelid='public.project_members'::regclass AND k.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='project_id' AND attnotnull),(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='user_id' AND attnotnull)]::smallint[] "
            "AND k.confkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=k.confrelid AND attname='project_id'),(SELECT attnum FROM pg_attribute WHERE attrelid=k.confrelid AND attname='user_id')]::smallint[]) AS membership_fk "
            "FROM pg_class c WHERE c.oid=to_regclass('paperpilot_business.project_logins') AND c.relkind='r'",
            (cfg.binding_owner,),
        ).fetchone()
        if not binding or not all(binding.values()):
            raise PermissionError("Audit binding table invalid")

    def _verify_functions(self, conn):
        cfg = self.config
        root = Path(__file__).parents[1]
        definitions = (
            (
                root / "security/migrations/012_candidate_business_active_identity.sql",
                "bound_project",
                "paperpilot_business.bound_project()",
                cfg.function_owner,
                "uuid",
                "CREATE OR REPLACE",
            ),
            (
                root / "security/migrations/012_candidate_business_active_identity.sql",
                "bound_identity",
                "paperpilot_business.bound_identity(uuid,uuid)",
                cfg.function_owner,
                "boolean",
                "CREATE OR REPLACE",
            ),
            (
                root / "data/migrations/006_audit_read_boundary.sql",
                "event_fingerprint",
                "paperpilot_audit.event_fingerprint(uuid)",
                FINGERPRINT_OWNER,
                "character varying",
                "CREATE",
            ),
        )
        for path, name, signature, owner, rettype, verb in definitions:
            source = path.read_text(encoding="utf-8-sig")
            expected = re.search(
                verb + r" FUNCTION [^.]+\." + name + r"\([^;]*?AS \$\$(.*?)\$\$;", source, re.S
            )
            item = conn.execute(
                "SELECT p.prosrc,pg_get_userbyid(p.proowner)=%s AS owner_ok,p.prosecdef AS definer, "
                "p.provolatile='s' AS stable,p.proconfig=ARRAY['search_path=pg_catalog'] AS safe_path, "
                "has_function_privilege(current_user,p.oid,'EXECUTE') AS callable, "
                "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
                "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS no_public, "
                "pg_get_function_result(p.oid)=%s AS result_ok FROM pg_proc p "
                "WHERE p.oid=to_regprocedure(%s) AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')",
                (owner, rettype, signature),
            ).fetchone()
            if (
                not expected
                or not item
                or item["prosrc"] != expected.group(1)
                or not all(
                    item[key]
                    for key in (
                        "owner_ok",
                        "definer",
                        "stable",
                        "safe_path",
                        "callable",
                        "no_public",
                        "result_ok",
                    )
                )
            ):
                raise PermissionError("Audit function invalid")

    def ready(self):
        with self.database.transaction():
            return True

    def append_audit(self, event):
        if event.actor_id != self.config.user_id or event.project_id != self.config.project_id:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        return self._data.append_audit(event)

    def close(self):
        self.database.close()
