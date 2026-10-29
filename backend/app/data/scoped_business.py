"""Explicit isolated per-login business-data pilot, not a complete DataPort binding.

Global project creation, discovery, background workers, writes and app composition
remain outside this candidate. PostgreSQL RLS is the data isolation enforcement.
"""

from dataclasses import dataclass, field
from pathlib import Path
import re
from uuid import UUID

from app.adapters.ports import Context
from app.data.adapter import PostgresData
from app.data.database import ConnectionConfig, PostgresDatabase
from app.data.scoped_project_creator import OWNER as BOOTSTRAP_OWNER
from app.data.scoped_project_creator import POLICIES as BOOTSTRAP_POLICIES
from app.data.settings import DataSettings
from app.errors import AppError

PROJECT_TABLES = frozenset(
    {
        "projects",
        "project_members",
        "papers",
        "project_papers",
        "authors",
        "paper_authors",
        "topics",
        "paper_topics",
        "chunks",
        "embedding_models",
        "chunk_embeddings",
        "tasks",
        "task_papers",
        "task_attempts",
        "task_results",
        "evidence_fields",
        "result_sources",
        "reports",
        "report_versions",
        "report_version_sources",
        "idempotency_records",
        "outbox_events",
    }
)

# Exact columns needed by the isolated upload and task-creation candidate.
INSERT_COLUMNS = {
    "papers": {"metadata_id", "project_id", "title", "year", "created_at"},
    "project_papers": {
        "paper_id",
        "project_id",
        "metadata_id",
        "imported_by",
        "digest",
        "storage_key",
        "status",
        "index_state",
        "created_at",
        "updated_at",
    },
    "tasks": {
        "task_id",
        "project_id",
        "actor_id",
        "request_id",
        "kind",
        "status",
        "attempts",
        "payload",
        "payload_fingerprint",
        "created_at",
        "updated_at",
    },
    "task_papers": {"task_id", "paper_id", "project_id"},
    "idempotency_records": {
        "user_id",
        "project_id",
        "operation",
        "key",
        "request_fingerprint",
        "response_snapshot",
        "paper_id",
        "task_id",
        "version_id",
    },
    "outbox_events": {"event_id", "project_id", "actor_id", "request_id", "payload"},
}
ACTOR_CHECKS = {
    "project_papers": "imported_by",
    "tasks": "actor_id",
    "idempotency_records": "user_id",
    "outbox_events": "actor_id",
}
REPORT_INSERT_COLUMNS = {
    "reports": {"report_id", "project_id", "created_by", "current_version_id", "created_at"},
    "report_versions": {
        "version_id",
        "project_id",
        "report_id",
        "number",
        "base_version_id",
        "title",
        "research_question",
        "body",
        "created_by",
        "created_at",
    },
    "report_version_sources": {
        "version_id",
        "citation_id",
        "project_id",
        "ordinal",
        "paper_id",
        "chunk_id",
        "page_number",
        "excerpt",
        "synthetic",
    },
}
REPORT_UPDATE_COLUMNS = {"reports": {"current_version_id"}}
REPORT_ACTOR_TABLES = frozenset({"reports", "report_versions"})
WORKER_ACTOR_CHECKS = {**ACTOR_CHECKS, "task_attempts": "actor_id"}
WORKER_INSERT_COLUMNS = {
    "task_attempts": {
        "task_id",
        "attempt_no",
        "project_id",
        "actor_id",
        "request_id",
        "status",
        "claimed_at",
        "lease_expires_at",
    }
}
WORKER_UPDATE_COLUMNS = {
    "tasks": {"status", "attempts", "recovery_failures", "updated_at"},
    "task_attempts": {"status", "finished_at"},
    "project_papers": {"status", "updated_at"},
}
FAILURE_INSERT_COLUMNS = {**WORKER_INSERT_COLUMNS, "outbox_events": INSERT_COLUMNS["outbox_events"]}
FAILURE_UPDATE_COLUMNS = {
    **WORKER_UPDATE_COLUMNS,
    "tasks": WORKER_UPDATE_COLUMNS["tasks"] | {"error_code", "error_message"},
    "task_attempts": WORKER_UPDATE_COLUMNS["task_attempts"] | {"error_code", "error_message"},
}
RESULT_INSERT_COLUMNS = {
    **FAILURE_INSERT_COLUMNS,
    "chunks": {
        "chunk_id",
        "project_id",
        "paper_id",
        "page_number",
        "excerpt",
        "content",
        "synthetic",
        "created_at",
    },
    "task_results": {
        "task_id",
        "project_id",
        "kind",
        "committed_attempt",
        "paper_id",
        "evidence_id",
        "payload",
        "result_fingerprint",
        "created_at",
    },
    "evidence_fields": {"task_id", "field_name", "project_id", "text", "status"},
    "result_sources": {
        "task_id",
        "scope",
        "citation_id",
        "project_id",
        "field_name",
        "ordinal",
        "paper_id",
        "chunk_id",
        "page_number",
        "excerpt",
        "synthetic",
    },
}
RESULT_UPDATE_COLUMNS = {
    **FAILURE_UPDATE_COLUMNS,
    "project_papers": FAILURE_UPDATE_COLUMNS["project_papers"] | {"index_state", "active_process_task_id"},
}
RESULT_POLICY_CHECKS = {
    "task_results": "paperpilot_business.worker_has_lease(task_id, project_id, committed_attempt)",
    "evidence_fields": "paperpilot_business.worker_has_result(task_id, project_id)",
    "result_sources": "paperpilot_business.worker_has_result(task_id, project_id)",
    "chunks": "paperpilot_business.worker_has_process(paper_id, project_id)",
}


@dataclass(frozen=True)
class ScopedBusinessConfig:
    connection: ConnectionConfig = field(repr=False)
    user_id: UUID
    project_id: UUID
    function_owner: str
    binding_owner: str


class ScopedBusinessReader:
    """Read-only candidate bound to exactly one server-configured user and project."""

    insert_columns = {}
    update_columns = {}
    actor_checks = {}
    require_report_actor_checks = False
    require_result_checks = False

    def __init__(self, config: ScopedBusinessConfig):
        if (
            config.connection.host not in {"127.0.0.1", "localhost", "::1"}
            or not isinstance(config.user_id, UUID)
            or not isinstance(config.project_id, UUID)
            or not config.function_owner
            or not config.binding_owner
        ):
            raise ValueError("Invalid isolated business configuration")
        self.config = config
        self.database = PostgresDatabase(config.connection, pool_size=1)
        self.database.set_transaction_guard(self._verify)
        self._data = PostgresData(self.database, DataSettings())

    def _verify(self, conn):
        try:
            self._verify_database(conn)
        except Exception:
            raise AppError(503, "DATA_UNAVAILABLE", "项目数据库隔离配置不可用") from None

    def _verify_database(self, conn):
        cfg = self.config
        if conn.info.server_version // 10000 != 16:
            raise PermissionError("Unreviewed PostgreSQL version")
        role = conn.execute(
            "SELECT current_user=session_user AND current_user=%s AS same_user, "
            "rolcanlogin AND NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolinherit) AS role_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) AS members_ok, "
            "NOT has_database_privilege(current_user,current_database(),'CREATE,TEMP') AS db_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
            "AND has_schema_privilege(current_user,n.oid,'CREATE')) AS schemas_ok "
            "FROM pg_roles r WHERE r.rolname=current_user",
            (cfg.connection.user,),
        ).fetchone()
        if not role or not all(role.values()):
            raise PermissionError("Unsafe business login")
        grants = conn.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f') "
            "AND (has_table_privilege(current_user,c.oid,'DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            "OR has_any_column_privilege(current_user,c.oid,'REFERENCES') "
            "OR ((n.nspname<>'public' OR NOT c.relname=ANY(%s)) AND "
            "(has_table_privilege(current_user,c.oid,'UPDATE') OR has_any_column_privilege(current_user,c.oid,'UPDATE'))) "
            "OR ((n.nspname<>'public' OR NOT c.relname=ANY(%s)) AND "
            "(has_table_privilege(current_user,c.oid,'INSERT') OR has_any_column_privilege(current_user,c.oid,'INSERT'))) "
            "OR ((n.nspname<>'public' OR NOT (c.relname=ANY(%s) OR c.relname='users')) "
            "AND (has_table_privilege(current_user,c.oid,'SELECT') OR has_any_column_privilege(current_user,c.oid,'SELECT'))))) AS relations_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE left(n.nspname,3)<>'pg_' "
            "AND n.nspname<>'information_schema' AND c.relkind='S' AND has_sequence_privilege(current_user,c.oid,'USAGE,SELECT,UPDATE')) AS sequences_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND p.prosecdef "
            "AND p.oid NOT IN (to_regprocedure('paperpilot_business.bound_project()'),to_regprocedure('paperpilot_business.bound_identity(uuid,uuid)')) "
            "AND has_function_privilege(current_user,p.oid,'EXECUTE')) AS definer_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='pg_catalog' "
            "AND p.proname=ANY(ARRAY['pg_read_file','pg_read_binary_file','pg_ls_dir','pg_stat_file']) AND has_function_privilege(current_user,p.oid,'EXECUTE')) AS files_ok",
            (list(self.update_columns), list(self.insert_columns), list(PROJECT_TABLES)),
        ).fetchone()
        if not grants or not all(grants.values()):
            raise PermissionError("Business reader grants invalid")
        for privilege, profile in (("INSERT", self.insert_columns), ("UPDATE", self.update_columns)):
            for table, allowed_columns in profile.items():
                columns = conn.execute(
                    "SELECT a.attname,has_column_privilege(current_user,a.attrelid,a.attnum,%s) AS allowed "
                    "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped",
                    (privilege, table),
                ).fetchall()
                if {row["attname"] for row in columns if row["allowed"]} != allowed_columns:
                    raise PermissionError("Business column grants invalid")
        users = conn.execute(
            "SELECT attname,has_column_privilege(current_user,attrelid,attnum,'SELECT') AS allowed "
            "FROM pg_attribute WHERE attrelid='public.users'::regclass AND attnum>0 AND NOT attisdropped"
        ).fetchall()
        if {row["attname"] for row in users if row["allowed"]} != {"user_id", "username", "display_name"}:
            raise PermissionError("Business reader user columns invalid")
        policies = conn.execute(
            "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,pg_get_userbyid(c.relowner) AS owner, "
            "count(p.oid) AS policy_count, bool_and(p.polname='project_identity' AND p.polcmd='*' "
            "AND p.polpermissive AND p.polroles=ARRAY[0::oid] "
            "AND pg_get_expr(p.polqual,p.polrelid)='(project_id = paperpilot_business.bound_project())') "
            "FILTER (WHERE p.polname='project_identity') AS policy_ok, "
            "max(pg_get_expr(p.polwithcheck,p.polrelid)) FILTER (WHERE p.polname='project_identity') AS check_expression, "
            "bool_and(NOT p.polpermissive AND p.polcmd='a' AND p.polroles=ARRAY[0::oid] AND p.polqual IS NULL "
            "AND pg_get_expr(p.polwithcheck,p.polrelid)='paperpilot_business.bound_identity(created_by, project_id)') "
            "FILTER (WHERE p.polname='report_insert_actor') AS report_policy_ok, "
            "bool_and(NOT p.polpermissive AND p.polcmd='a' AND p.polroles=ARRAY[0::oid] AND p.polqual IS NULL) "
            "FILTER (WHERE p.polname='worker_result_actor') AS result_policy_ok, "
            "max(pg_get_expr(p.polwithcheck,p.polrelid)) FILTER (WHERE p.polname='worker_result_actor') AS result_expression, "
            "max(p.polname) FILTER (WHERE left(p.polname,10)='bootstrap_') AS bootstrap_name, "
            "bool_and(p.polpermissive AND p.polcmd='a' AND p.polqual IS NULL "
            "AND p.polroles=ARRAY[(SELECT oid FROM pg_roles WHERE rolname=%s)]::oid[]) "
            "FILTER (WHERE left(p.polname,10)='bootstrap_') AS bootstrap_policy_ok, "
            "max(pg_get_expr(p.polwithcheck,p.polrelid)) "
            "FILTER (WHERE left(p.polname,10)='bootstrap_') AS bootstrap_expression "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "LEFT JOIN pg_policy p ON p.polrelid=c.oid "
            "WHERE n.nspname='public' AND c.relname=ANY(%s) AND c.relkind IN ('r','p') "
            "GROUP BY c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relowner",
            (BOOTSTRAP_OWNER, list(PROJECT_TABLES)),
        ).fetchall()
        if {row["relname"] for row in policies} != PROJECT_TABLES or any(
            not row["relrowsecurity"]
            or not row["relforcerowsecurity"]
            or row["owner"] == cfg.connection.user
            or not self._policy_count_valid(row)
            or not row["policy_ok"]
            or not self._policy_check_valid(row["relname"], row["check_expression"])
            for row in policies
        ):
            raise PermissionError("Business RLS policies invalid")
        bootstrap_tables = {row["relname"] for row in policies if row["bootstrap_name"] is not None}
        if bootstrap_tables:
            if bootstrap_tables != set(BOOTSTRAP_POLICIES):
                raise PermissionError("Incomplete project bootstrap policies")
            bootstrap_owner = conn.execute(
                "SELECT NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication "
                "OR rolcanlogin OR rolinherit) AS role_ok, "
                "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) AS members_ok, "
                "NOT has_database_privilege(r.oid,current_database(),'CREATE,TEMP') AS db_ok, "
                "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' "
                "AND n.nspname<>'information_schema' AND has_schema_privilege(r.oid,n.oid,'CREATE')) AS schemas_ok "
                "FROM pg_roles r WHERE r.rolname=%s",
                (BOOTSTRAP_OWNER,),
            ).fetchone()
            if not bootstrap_owner or not all(bootstrap_owner.values()):
                raise PermissionError("Unsafe project bootstrap role")
        owner = conn.execute(
            "SELECT NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolcanlogin OR rolinherit) AS role_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) AS members_ok, "
            "NOT has_database_privilege(r.oid,current_database(),'CREATE,TEMP') AS db_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
            "AND has_schema_privilege(r.oid,n.oid,'CREATE')) AS schemas_ok, "
            "has_table_privilege(r.oid,'paperpilot_business.project_logins','SELECT') AS binding_read, "
            "NOT has_table_privilege(r.oid,'paperpilot_business.project_logins','INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS binding_readonly, "
            "NOT has_any_column_privilege(r.oid,'paperpilot_business.project_logins','INSERT,UPDATE,REFERENCES') AS columns_readonly, "
            "NOT has_table_privilege(r.oid,'public.users','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS users_no_table, "
            "NOT has_any_column_privilege(r.oid,'public.users','INSERT,UPDATE,REFERENCES') AS users_no_write, "
            "has_column_privilege(r.oid,'public.users','user_id','SELECT') AND "
            "has_column_privilege(r.oid,'public.users','disabled','SELECT') AND "
            "NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid='public.users'::regclass "
            "AND a.attnum>0 AND NOT a.attisdropped AND a.attname NOT IN ('user_id','disabled') "
            "AND has_column_privilege(r.oid,a.attrelid,a.attnum,'SELECT')) AS users_columns_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE left(n.nspname,3)<>'pg_' "
            "AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f') "
            "AND c.oid NOT IN ('paperpilot_business.project_logins'::regclass,'public.users'::regclass) AND (has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR has_any_column_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS other_objects_ok "
            "FROM pg_roles r WHERE r.rolname=%s",
            (cfg.function_owner,),
        ).fetchone()
        if not owner or not all(owner.values()):
            raise PermissionError("Business function owner invalid")
        binding = conn.execute(
            "SELECT pg_get_userbyid(c.relowner)=%s AS owner_ok, "
            "NOT has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS no_table, "
            "NOT has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES') AS no_columns, "
            "EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='p' "
            "AND k.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='login_role' AND attnotnull)]::smallint[]) AS unique_login, "
            "EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='f' AND k.convalidated AND k.confdeltype='c' "
            "AND k.confrelid='public.project_members'::regclass AND k.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='project_id' AND attnotnull),(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='user_id' AND attnotnull)]::smallint[] "
            "AND k.confkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=k.confrelid AND attname='project_id'),(SELECT attnum FROM pg_attribute WHERE attrelid=k.confrelid AND attname='user_id')]::smallint[]) AS membership_fk, "
            "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a "
            "WHERE a.grantee<>c.relowner AND a.privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER')) AS no_extra_writers, "
            "NOT EXISTS (SELECT 1 FROM pg_attribute a, LATERAL aclexplode(a.attacl) e "
            "WHERE a.attrelid=c.oid AND e.grantee<>c.relowner AND e.privilege_type IN ('INSERT','UPDATE','REFERENCES')) AS no_extra_column_writers "
            "FROM pg_class c WHERE c.oid=to_regclass('paperpilot_business.project_logins') AND c.relkind='r'",
            (cfg.binding_owner,),
        ).fetchone()
        if not binding or not all(binding.values()):
            raise PermissionError("Business binding table invalid")
        source = (
            Path(__file__).parents[1] / "security/migrations/012_candidate_business_active_identity.sql"
        ).read_text(encoding="utf-8-sig")
        for signature, name, rettype in (
            ("paperpilot_business.bound_project()", "bound_project", "uuid"),
            ("paperpilot_business.bound_identity(uuid,uuid)", "bound_identity", "boolean"),
        ):
            expected = re.search(
                r"CREATE OR REPLACE FUNCTION paperpilot_business\." + name + r"\([^;]*?AS \$\$(.*?)\$\$;",
                source,
                re.S,
            )
            item = conn.execute(
                "SELECT pg_get_userbyid(p.proowner) AS owner,p.prosecdef,p.provolatile='s' AS stable, "
                "p.proconfig=ARRAY['search_path=pg_catalog'] AS safe_path,p.prosrc, "
                "has_function_privilege(current_user,p.oid,'EXECUTE') AS can_execute, "
                "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS no_public, "
                "pg_get_function_result(p.oid)=%s AS result_ok FROM pg_proc p WHERE p.oid=to_regprocedure(%s) "
                "AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')",
                (rettype, signature),
            ).fetchone()
            if (
                not expected
                or not item
                or item["owner"] != cfg.function_owner
                or item["prosrc"] != expected.group(1)
                or not all(
                    item[k]
                    for k in ("prosecdef", "stable", "safe_path", "can_execute", "no_public", "result_ok")
                )
            ):
                raise PermissionError("Business binding function invalid")
        scope = conn.execute(
            "SELECT paperpilot_business.bound_identity(%s,%s) AS bound", (cfg.user_id, cfg.project_id)
        ).fetchone()
        if not scope or not scope["bound"]:
            raise PermissionError("Business identity binding invalid")
        if self.require_result_checks:
            self._verify_result_functions(conn)

    def _policy_count_valid(self, row):
        table, count = row["relname"], row["policy_count"]
        if table in BOOTSTRAP_POLICIES:
            if count == 1:
                return row["bootstrap_name"] is None
            name, expression = BOOTSTRAP_POLICIES[table]
            return (
                count == 2
                and row["bootstrap_name"] == name
                and row["bootstrap_policy_ok"] is True
                and row["bootstrap_expression"] == expression
            )
        if table in RESULT_POLICY_CHECKS:
            if count == 2:
                return (
                    row["result_policy_ok"] is True
                    and row["result_expression"] == RESULT_POLICY_CHECKS[table]
                )
            return count == 1 and not self.require_result_checks
        if table in REPORT_ACTOR_TABLES and self.require_report_actor_checks:
            return count == 2 and row["report_policy_ok"] is True
        return count == 1 or (table in REPORT_ACTOR_TABLES and count == 2 and row["report_policy_ok"] is True)

    def _verify_result_functions(self, conn):
        source = (
            Path(__file__).parents[1] / "security/migrations/009_candidate_worker_result_checks.sql"
        ).read_text(encoding="utf-8-sig")
        for name, signature in (
            ("worker_has_lease", "paperpilot_business.worker_has_lease(uuid,uuid,integer)"),
            ("worker_has_result", "paperpilot_business.worker_has_result(uuid,uuid)"),
            ("worker_has_process", "paperpilot_business.worker_has_process(uuid,uuid)"),
        ):
            expected = re.search(
                r"CREATE FUNCTION paperpilot_business\." + name + r"\([^;]*?AS \$\$(.*?)\$\$;", source, re.S
            )
            item = conn.execute(
                "SELECT NOT p.prosecdef AS invoker, p.provolatile='v' AS volatile, "
                "p.proconfig=ARRAY['search_path=pg_catalog'] AS safe_path, p.prosrc, "
                "pg_get_userbyid(p.proowner)=%s AS owner_ok, "
                "has_function_privilege(current_user,p.oid,'EXECUTE') AS can_execute, "
                "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
                "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS no_public "
                "FROM pg_proc p WHERE p.oid=to_regprocedure(%s) AND p.prorettype='boolean'::regtype "
                "AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')",
                (self.config.binding_owner, signature),
            ).fetchone()
            if (
                not expected
                or not item
                or item["prosrc"] != expected.group(1)
                or not all(
                    item[key]
                    for key in ("invoker", "volatile", "safe_path", "owner_ok", "can_execute", "no_public")
                )
            ):
                raise PermissionError("Worker result function invalid")

    def _policy_check_valid(self, table, actual):
        base = "(project_id = paperpilot_business.bound_project())"
        actor_column = WORKER_ACTOR_CHECKS.get(table)
        actor = (
            "((project_id = paperpilot_business.bound_project()) AND "
            f"paperpilot_business.bound_identity({actor_column}, project_id))"
        )
        if table in self.actor_checks:
            return actual == actor
        return actual == base or (actor_column is not None and actual == actor)

    def ready(self):
        with self.database.transaction():
            pass
        return True

    def close(self):
        self.database.close()

    def _check_context(self, ctx):
        if (
            not isinstance(ctx, Context)
            or ctx.user_id != self.config.user_id
            or ctx.project_id != self.config.project_id
            or not isinstance(ctx.request_id, UUID)
        ):
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")

    def papers(self, ctx, year_from=None, year_to=None):
        self._check_context(ctx)
        return self._data.papers(ctx, year_from, year_to)

    def role(self, user_id, project_id):
        if user_id != self.config.user_id or project_id != self.config.project_id:
            return None
        return self._data.role(user_id, project_id)

    def paper(self, ctx, paper_id):
        self._check_context(ctx)
        return self._data.paper(ctx, paper_id)

    def sources(self, ctx, paper_id):
        self._check_context(ctx)
        return self._data.sources(ctx, paper_id)

    def task(self, ctx, task_id):
        self._check_context(ctx)
        return self._data.task(ctx, task_id)

    def reports(self, ctx):
        self._check_context(ctx)
        return self._data.reports(ctx)

    def versions(self, ctx, report_id):
        self._check_context(ctx)
        return self._data.versions(ctx, report_id)


class ScopedBusinessWriter(ScopedBusinessReader):
    """Actor-bound import/task candidate; upload is wired only in the isolated pilot."""

    insert_columns = INSERT_COLUMNS
    actor_checks = ACTOR_CHECKS

    def import_paper(self, ctx, title, year, digest, file, key):
        self._check_context(ctx)
        return self._data.import_paper(ctx, title, year, digest, file, key)

    def create_task(self, ctx, kind, payload, key):
        self._check_context(ctx)
        return self._data.create_task(ctx, kind, payload, key)


class ScopedReportWriter(ScopedBusinessWriter):
    """Report writes in an isolated, actor-bound test database only."""

    insert_columns = {**INSERT_COLUMNS, **REPORT_INSERT_COLUMNS}
    update_columns = REPORT_UPDATE_COLUMNS
    require_report_actor_checks = True

    def save_report(self, ctx, data, key):
        self._check_context(ctx)
        return self._data.save_report(ctx, data, key)


class ScopedClaimWorker(ScopedBusinessReader):
    """Isolated per-user/project task-claim candidate; no result commit yet."""

    insert_columns = WORKER_INSERT_COLUMNS
    update_columns = WORKER_UPDATE_COLUMNS
    actor_checks = WORKER_ACTOR_CHECKS

    def __init__(self, config: ScopedBusinessConfig):
        super().__init__(config)
        self._data = PostgresData(
            self.database, DataSettings(), claim_scope=(config.project_id, config.user_id)
        )

    def claim_job(self):
        return self._data.claim_job()


class ScopedFailureWorker(ScopedClaimWorker):
    """Isolated claim and failure candidate; successful result commits remain disabled."""

    insert_columns = FAILURE_INSERT_COLUMNS
    update_columns = FAILURE_UPDATE_COLUMNS

    def fail_job(self, job, code, message):
        self._check_context(job.context)
        return self._data.fail_job(job, code, message)


class ScopedResultWorker(ScopedFailureWorker):
    """Isolated worker candidate; not registered as a background worker."""

    insert_columns = RESULT_INSERT_COLUMNS
    update_columns = RESULT_UPDATE_COLUMNS
    require_result_checks = True

    def complete_job(self, job, result):
        self._check_context(job.context)
        return self._data.complete_job(job, result)


class ScopedBusinessRouter:
    """Route pilot operations only to explicitly bound isolated database identities."""

    def __init__(self, readers):
        readers = tuple(readers)
        if not readers:
            raise ValueError("At least one scoped business identity is required")
        first = readers[0].config.connection
        self._readers = {}
        roles = set()
        for reader in readers:
            cfg = reader.config
            connection = cfg.connection
            key = (cfg.user_id, cfg.project_id)
            if (
                (connection.host, connection.port, connection.dbname)
                != (first.host, first.port, first.dbname)
                or key in self._readers
                or connection.user in roles
            ):
                raise ValueError("Invalid scoped business identity configuration")
            self._readers[key] = reader
            roles.add(connection.user)

    def ready(self):
        for reader in self._readers.values():
            if not reader.ready():
                return False
        return True

    def close(self):
        first_error = None
        for reader in self._readers.values():
            try:
                reader.close()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def _reader(self, ctx):
        if not isinstance(ctx, Context) or not isinstance(ctx.request_id, UUID):
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        reader = self._readers.get((ctx.user_id, ctx.project_id))
        if reader is None:
            raise AppError(503, "DATA_UNAVAILABLE", "项目数据库身份不可用")
        return reader

    def validate_worker(self, worker):
        if not isinstance(worker, ScopedResultWorker):
            raise ValueError("Invalid scoped worker")
        cfg = worker.config
        reader = self._readers.get((cfg.user_id, cfg.project_id))
        if reader is None:
            raise ValueError("Scoped worker has no matching reader")
        target = (cfg.connection.host, cfg.connection.port, cfg.connection.dbname)
        reader_target = (
            reader.config.connection.host,
            reader.config.connection.port,
            reader.config.connection.dbname,
        )
        reader_roles = {entry.config.connection.user for entry in self._readers.values()}
        if target != reader_target or cfg.connection.user in reader_roles:
            raise ValueError("Scoped worker requires a separate database identity")

    def writer_for(self, ctx):
        writer = self._reader(ctx)
        if not isinstance(writer, ScopedBusinessWriter):
            raise AppError(503, "DATA_UNAVAILABLE", "项目数据库写入身份不可用")
        return writer

    def report_writer_for(self, ctx):
        writer = self._reader(ctx)
        if not isinstance(writer, ScopedReportWriter):
            raise AppError(503, "DATA_UNAVAILABLE", "项目报告写入身份不可用")
        return writer

    @property
    def supports_upload(self):
        return any(isinstance(reader, ScopedBusinessWriter) for reader in self._readers.values())

    @property
    def supports_reports(self):
        return any(isinstance(reader, ScopedReportWriter) for reader in self._readers.values())

    def papers(self, ctx, year_from=None, year_to=None):
        return self._reader(ctx).papers(ctx, year_from, year_to)

    def paper(self, ctx, paper_id):
        return self._reader(ctx).paper(ctx, paper_id)

    def sources(self, ctx, paper_id):
        return self._reader(ctx).sources(ctx, paper_id)

    def task(self, ctx, task_id):
        return self._reader(ctx).task(ctx, task_id)

    def reports(self, ctx):
        return self._reader(ctx).reports(ctx)

    def versions(self, ctx, report_id):
        return self._reader(ctx).versions(ctx, report_id)
