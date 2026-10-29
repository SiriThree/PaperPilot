"""Experimental, explicitly injected restricted PostgreSQL executor.

Not wired into production or the default factory. A/D must review role provisioning,
project binding and operational limits before any deployment.
"""

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from math import isfinite
from uuid import UUID

import psycopg
from psycopg import sql

from app.adapters.ports import Context
from app.security.adapter import SecurityAdapter
from app.security.sql_policy import QueryPlan, SQL_BY_QUESTION, prepare_query
from app.security.view_baseline import BINDING_BODY, POSTGRES_MAJOR, VIEW_SHA256
from app.secret_config import secret_value


@dataclass(frozen=True)
class RestrictedConnection:
    project_id: UUID
    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False)
    view_owner: str = ""
    statistics_sha256: str = ""
    topics_sha256: str = ""

    @classmethod
    def from_env(cls):
        names = (
            "PROJECT_ID",
            "HOST",
            "PORT",
            "DBNAME",
            "USER",
            "PASSWORD",
            "VIEW_OWNER",
            "STATISTICS_SHA256",
            "TOPICS_SHA256",
        )
        values = [
            secret_value(os.environ, "PAPERPILOT_SECURITY_DB_PASSWORD")
            if name == "PASSWORD"
            else os.getenv("PAPERPILOT_SECURITY_DB_" + name, "")
            for name in names
        ]
        if os.getenv("PAPERPILOT_SECURITY_ISOLATED_TEST") != "1" or not all(values):
            raise RuntimeError("Explicit isolated restricted database configuration is required")
        try:
            project, host, port, dbname, user, password, owner, statistics, topics = values
            if host not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("non-local host")
            return cls(UUID(project), host, int(port), dbname, user, password, owner, statistics, topics)
        except ValueError:
            raise RuntimeError("Invalid isolated restricted database configuration") from None


def restricted_configurations(environ):
    """Resolve isolated project logins from trusted server configuration only."""
    raw = secret_value(environ, "PAPERPILOT_SECURITY_DB_BINDINGS")
    if not raw:
        return [RestrictedConnection.from_env()]
    if environ.get("PAPERPILOT_SECURITY_ISOLATED_TEST") != "1":
        raise RuntimeError("Explicit isolated restricted database configuration is required")
    if any(
        environ.get("PAPERPILOT_SECURITY_DB_" + name)
        for name in ("PROJECT_ID", "USER", "PASSWORD", "PASSWORD_FILE")
    ):
        raise RuntimeError("Conflicting restricted database configuration")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        if len(raw) > 65536:
            raise ValueError("oversized bindings")
        entries = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(entries, list) or not 1 <= len(entries) <= 64:
            raise ValueError("invalid bindings")
        common = [
            environ.get("PAPERPILOT_SECURITY_DB_" + key, "")
            for key in ("HOST", "PORT", "DBNAME", "VIEW_OWNER", "STATISTICS_SHA256", "TOPICS_SHA256")
        ]
        host, port, dbname, owner, statistics, topics = common
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("non-local host")
        configs = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"project_id", "user", "password"}:
                raise ValueError("invalid binding")
            config = RestrictedConnection(
                UUID(entry["project_id"]),
                host,
                int(port),
                dbname,
                entry["user"],
                entry["password"],
                owner,
                statistics,
                topics,
            )
            RestrictedExecutor(config)  # apply the same pinned baseline and validation as the executor
            configs.append(config)
        if len({c.project_id for c in configs}) != len(configs) or len({c.user for c in configs}) != len(
            configs
        ):
            raise ValueError("duplicate binding")
        return configs
    except (ValueError, TypeError, KeyError, AttributeError):
        raise RuntimeError("Invalid isolated restricted database configuration") from None


def _bounded_results(columns, raw_rows):
    if len(raw_rows) >= 100:
        raise ValueError("Result may exceed row budget")
    if len(columns) != len(set(columns)):
        raise ValueError("Duplicate result columns")
    rows = []
    for raw in raw_rows:
        row = {}
        for column, value in zip(columns, raw, strict=True):
            if isinstance(value, UUID):
                value = str(value)
            if value is not None and type(value) not in (str, int, float, bool):
                raise ValueError("Unsupported result type")
            if isinstance(value, float) and not isfinite(value):
                raise ValueError("Non-finite result")
            row[column] = value
        rows.append(row)
    if (
        len(
            json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False, allow_nan=False).encode(
                "utf-8"
            )
        )
        > 256 * 1024
    ):
        raise ValueError("Result exceeds response budget")
    return columns, rows


class RestrictedExecutor:
    """Single-project candidate: one short-lived connection per request, never pooled."""

    def __init__(self, config: RestrictedConnection):
        if (
            not isinstance(config.project_id, UUID)
            or not all(
                isinstance(v, str) and v.strip()
                for v in (config.host, config.dbname, config.user, config.password)
            )
            or config.statistics_sha256 != VIEW_SHA256["paper_statistics"]
            or config.topics_sha256 != VIEW_SHA256["paper_topics"]
            or not 1 <= config.port <= 65535
            or not config.view_owner.strip()
            or not all(
                re.fullmatch(r"[0-9a-f]{64}", value)
                for value in (config.statistics_sha256, config.topics_sha256)
            )
        ):
            raise ValueError("Invalid restricted connection configuration")
        self.config = config
        self._ready = False
        self._closed = False

    async def _connect(self):
        return await psycopg.AsyncConnection.connect(
            host=self.config.host,
            port=self.config.port,
            dbname=self.config.dbname,
            user=self.config.user,
            password=self.config.password,
            connect_timeout=3,
        )

    async def _check_role(self, connection):
        if connection.info.server_version // 10000 != POSTGRES_MAJOR:
            raise PermissionError("Unsupported security baseline PostgreSQL version")
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT current_user = session_user, NOT (rolsuper OR rolbypassrls OR rolcreatedb "
                "OR rolcreaterole OR rolreplication OR rolinherit), NOT EXISTS (SELECT 1 FROM pg_auth_members "
                "WHERE member = r.oid), "
                "NOT has_database_privilege(current_user, current_database(), 'CREATE,TEMP'), "
                "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3) <> 'pg_' "
                "AND n.nspname <> 'information_schema' AND has_schema_privilege(current_user,n.oid,'CREATE')) "
                "FROM pg_roles r WHERE rolname = current_user"
            )
            role = await cursor.fetchone()
            if not role or not all(role):
                raise PermissionError("Restricted role verification failed")
            await cursor.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE left(n.nspname,3) <> 'pg_' AND n.nspname <> 'information_schema' "
                "AND c.relkind IN ('r','p','v','m','f') "
                "AND NOT (n.nspname='paperpilot_security' AND c.relname IN ('paper_statistics','paper_topics')) "
                "AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                "OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))"
            )
            if not (await cursor.fetchone())[0]:
                raise PermissionError("Restricted role has forbidden table privileges")
            await cursor.execute(
                "SELECT NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolcanlogin OR rolinherit), "
                "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid), "
                "NOT has_database_privilege(r.oid, current_database(), 'CREATE,TEMP'), "
                "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3) <> 'pg_' "
                "AND n.nspname <> 'information_schema' AND has_schema_privilege(r.oid,n.oid,'CREATE')) "
                "FROM pg_roles r WHERE rolname=%s",
                (self.config.view_owner,),
            )
            owner = await cursor.fetchone()
            if not owner or not all(owner):
                raise PermissionError("Security owner verification failed")
            await cursor.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE left(n.nspname,3) <> 'pg_' AND n.nspname <> 'information_schema' "
                "AND c.relkind IN ('r','p','v','m','f') "
                "AND NOT (n.nspname='paperpilot_security' AND c.relname IN ('paper_statistics','paper_topics')) "
                "AND ((n.nspname='paperpilot_security' AND c.relname='project_readers') "
                "OR (n.nspname='public' AND c.relname IN ('project_papers','papers','task_results','paper_topics','topics'))) "
                "AND (NOT has_table_privilege(%s,c.oid,'SELECT') "
                "OR has_table_privilege(%s,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                "OR has_any_column_privilege(%s,c.oid,'INSERT,UPDATE,REFERENCES'))) ",
                (self.config.view_owner, self.config.view_owner, self.config.view_owner),
            )
            if not (await cursor.fetchone())[0]:
                raise PermissionError("Security owner has forbidden permissions")
            await cursor.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE left(n.nspname,3) <> 'pg_' AND n.nspname <> 'information_schema' "
                "AND c.relkind IN ('r','p','v','m','f') "
                "AND NOT ((n.nspname='paperpilot_security' AND c.relname IN ('paper_statistics','paper_topics','project_readers')) "
                "OR (n.nspname='public' AND c.relname IN ('project_papers','papers','task_results','paper_topics','topics'))) "
                "AND (has_table_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                "OR has_any_column_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) ",
                (self.config.view_owner, self.config.view_owner),
            )
            if not (await cursor.fetchone())[0]:
                raise PermissionError("Security owner has forbidden object privileges")
            await cursor.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE left(n.nspname,3) <> 'pg_' AND n.nspname <> 'information_schema' "
                "AND CASE WHEN c.relkind='S' THEN "
                "(has_sequence_privilege(current_user,c.oid,'USAGE,SELECT,UPDATE') "
                "OR has_sequence_privilege(%s,c.oid,'USAGE,SELECT,UPDATE')) ELSE false END) ",
                (self.config.view_owner,),
            )
            if not (await cursor.fetchone())[0]:
                raise PermissionError("Unexpected sequence privileges")
            await cursor.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE left(n.nspname,3) <> 'pg_' AND n.nspname <> 'information_schema' AND p.prosecdef "
                "AND p.oid <> COALESCE(to_regprocedure('paperpilot_security.binding_matches(uuid)')::oid,0::oid) "
                "AND (has_function_privilege(current_user,p.oid,'EXECUTE') "
                "OR has_function_privilege(%s,p.oid,'EXECUTE'))) ",
                (self.config.view_owner,),
            )
            if not (await cursor.fetchone())[0]:
                raise PermissionError("Unexpected security definer function privileges")
            # PostgreSQL exposes several privileged server-file functions in pg_catalog.
            # They are SECURITY INVOKER, so checking SECURITY DEFINER functions alone
            # cannot detect an explicit EXECUTE grant to this otherwise restricted login.
            await cursor.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='pg_catalog' AND p.proname = ANY(%s) "
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
                    self.config.view_owner,
                ),
            )
            if not (await cursor.fetchone())[0]:
                raise PermissionError("Unexpected server file function privileges")
            await cursor.execute(
                "SELECT c.relkind IN ('r','p'), "
                "(SELECT count(*)=1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='p' "
                "AND k.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid "
                "AND attname='login_role' AND attnotnull)]::smallint[]), "
                "EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid "
                "AND a.attname='project_id' AND a.attnotnull AND NOT a.attisdropped), "
                "EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='f' AND k.convalidated "
                "AND k.confrelid=to_regclass('public.projects') "
                "AND k.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='project_id')]::smallint[] "
                "AND k.confkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=k.confrelid AND attname='project_id')]::smallint[]) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='paperpilot_security' AND c.relname='project_readers'"
            )
            binding_table = await cursor.fetchone()
            if not binding_table or not all(binding_table):
                raise PermissionError("Binding table integrity verification failed")
            for name, expected_digest in (
                ("paper_statistics", self.config.statistics_sha256),
                ("paper_topics", self.config.topics_sha256),
            ):
                await cursor.execute(
                    "SELECT c.relowner <> (SELECT oid FROM pg_roles WHERE rolname=current_user), "
                    "has_table_privilege(current_user,c.oid,'SELECT'), "
                    "NOT has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                    "AND NOT has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES'), "
                    "'security_barrier=true' = ANY(COALESCE(c.reloptions, ARRAY[]::text[])), "
                    "pg_get_userbyid(c.relowner), pg_get_viewdef(c.oid, false), "
                    "NOT ('security_invoker=true' = ANY(COALESCE(c.reloptions, ARRAY[]::text[]))) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='paperpilot_security' AND c.relname=%s AND c.relkind='v'",
                    (name,),
                )
                row = await cursor.fetchone()
                if (
                    not row
                    or not all(row[:4])
                    or row[4] != self.config.view_owner
                    or not row[5]
                    or not row[6]
                ):
                    raise PermissionError("Restricted view verification failed")
                actual = hashlib.sha256(row[5].encode("utf-8")).hexdigest()
                if actual != expected_digest:
                    raise PermissionError("Restricted view verification failed")
                await cursor.execute(
                    "SELECT DISTINCT n.nspname,c.relname FROM pg_rewrite w "
                    "JOIN pg_depend d ON d.classid='pg_rewrite'::regclass AND d.objid=w.oid "
                    "AND d.refclassid='pg_class'::regclass "
                    "JOIN pg_class c ON c.oid=d.refobjid JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE w.ev_class=to_regclass(%s) AND c.oid<>w.ev_class",
                    (f"paperpilot_security.{name}",),
                )
                expected = {
                    ("paperpilot_security", "project_readers"),
                    ("public", "project_papers"),
                }
                expected.update(
                    {("public", "papers"), ("public", "task_results")}
                    if name == "paper_statistics"
                    else {("public", "paper_topics"), ("public", "topics")}
                )
                if set(await cursor.fetchall()) != expected:
                    raise PermissionError("Restricted view dependencies verification failed")
                # This detects exposed foreign rows, but cannot prove an empty binding is correct.
                await cursor.execute(
                    sql.SQL("SELECT EXISTS (SELECT 1 FROM {}.{} WHERE project_id <> %s LIMIT 1)").format(
                        sql.Identifier("paperpilot_security"), sql.Identifier(name)
                    ),
                    (self.config.project_id,),
                )
                if (await cursor.fetchone())[0]:
                    raise PermissionError("Restricted view exposes another project")
            await cursor.execute(
                "SELECT p.prosecdef, p.provolatile = 's', p.proconfig = ARRAY['search_path=pg_catalog'], "
                "pg_get_userbyid(p.proowner), p.prosrc, "
                "has_function_privilege(current_user, p.oid, 'EXECUTE'), "
                "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f',p.proowner))) a "
                "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='paperpilot_security' AND p.oid=to_regprocedure('paperpilot_security.binding_matches(uuid)') "
                "AND p.prorettype='boolean'::regtype AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')"
            )
            verifier = await cursor.fetchone()
            if (
                not verifier
                or not all(verifier[:3])
                or verifier[3] != self.config.view_owner
                or verifier[4] != BINDING_BODY
                or not all(verifier[5:])
            ):
                raise PermissionError("Binding verifier verification failed")
            await cursor.execute("SELECT paperpilot_security.binding_matches(%s)", (self.config.project_id,))
            if not (await cursor.fetchone())[0]:
                raise PermissionError("Restricted project binding mismatch")

    async def ready(self):
        """Explicit preflight, using the restricted identity, never an administrator."""
        if self._closed:
            raise RuntimeError("Executor is closed")
        self._ready = False
        async with asyncio.timeout(5):
            async with await self._connect() as connection:
                async with connection.transaction():
                    await connection.execute("SET TRANSACTION READ ONLY")
                    await connection.execute("SET LOCAL search_path TO pg_catalog, public")
                    await self._check_role(connection)
        self._ready = True
        return True

    def close(self):
        self._ready = False
        self._closed = True

    async def __call__(self, ctx: Context, plan: QueryPlan):
        if self._closed or not self._ready:
            raise RuntimeError("Restricted executor is not ready")
        if (
            not isinstance(ctx, Context)
            or not all(isinstance(v, UUID) for v in (ctx.user_id, ctx.project_id, ctx.request_id))
            or ctx.project_id != self.config.project_id
        ):
            raise PermissionError("Invalid project context")
        sort_columns = {
            "按年份统计论文数量": "year",
            "按研究主题统计论文数量": "topic",
            "查询尚未完成证据提取的论文": "paper_id",
        }
        matching_sort = next(
            (
                sort_columns[question]
                for question, template in SQL_BY_QUESTION.items()
                if question in sort_columns
                and isinstance(plan, QueryPlan)
                and plan == prepare_query(template, ctx.project_id)
            ),
            None,
        )
        if matching_sort is None:
            raise PermissionError("Query plan is not an authorized template")
        try:
            async with asyncio.timeout(5):
                async with await self._connect() as connection:
                    async with connection.transaction():
                        await connection.execute("SET TRANSACTION READ ONLY")
                        await connection.execute("SET LOCAL search_path TO pg_catalog, public")
                        await connection.execute(
                            "SELECT set_config('statement_timeout', %s, true)", ("3000",)
                        )
                        await self._check_role(connection)
                        async with connection.cursor() as cursor:
                            # The binding decision and rows share one PostgreSQL statement snapshot.
                            # plan.sql has already been matched against a reconstructed trusted template.
                            statement = sql.SQL(
                                "SELECT binding.ok, result.* FROM "
                                "(SELECT paperpilot_security.binding_matches(%(project_id)s) AS ok) binding "
                                "LEFT JOIN LATERAL (SELECT true AS present, value.* FROM ({}) value "
                                "WHERE binding.ok) result ON true ORDER BY result.{}"
                            ).format(sql.SQL(plan.sql), sql.Identifier(matching_sort))
                            await cursor.execute(statement, plan.parameters)
                            columns = [column.name for column in cursor.description][2:]
                            fetched = await cursor.fetchmany(100)
                            if not fetched or any(not row[0] for row in fetched):
                                raise PermissionError("Restricted project binding changed")
                            raw_rows = [row[2:] for row in fetched if row[1] is True]
                            # LIMIT 100 cannot distinguish exactly 100 from a truncation.
                            result = _bounded_results(columns, raw_rows)
                            await cursor.execute(
                                "SELECT paperpilot_security.binding_matches(%s)", (ctx.project_id,)
                            )
                            if not (await cursor.fetchone())[0]:
                                raise PermissionError("Restricted project binding changed")
                            return result
        except psycopg.errors.QueryCanceled:
            raise TimeoutError("Restricted query timed out") from None
        except (psycopg.Error, PermissionError, TimeoutError):
            self._ready = False
            raise


class RestrictedExecutorRouter:
    """Dispatch by trusted project context to separately bound, non-pooled logins."""

    def __init__(self, configs):
        if not configs or len({c.project_id for c in configs}) != len(configs):
            raise ValueError("Invalid restricted executor bindings")
        if len({c.user for c in configs}) != len(configs):
            raise ValueError("Restricted identities must be unique")
        self._executors = {c.project_id: RestrictedExecutor(c) for c in configs}
        self._closed = False

    @property
    def _ready(self):
        return not self._closed and all(executor._ready for executor in self._executors.values())

    async def ready(self):
        if self._closed:
            raise RuntimeError("Executor is closed")
        try:
            for executor in self._executors.values():
                await executor.ready()
        except BaseException:
            for executor in self._executors.values():
                executor.close()
            self._closed = True
            raise
        return True

    def close(self):
        for executor in self._executors.values():
            executor.close()
        self._closed = True

    async def __call__(self, ctx: Context, plan: QueryPlan):
        if self._closed or not isinstance(ctx, Context):
            raise PermissionError("Invalid project context")
        executor = self._executors.get(ctx.project_id)
        if executor is None:
            raise PermissionError("No restricted identity for project")
        return await executor(ctx, plan)


def candidate_security_factory(executor: RestrictedExecutor):
    """Explicit test/review injection after preflight; no default registration."""
    if not isinstance(executor, RestrictedExecutor) or not executor._ready or executor._closed:
        raise RuntimeError("Candidate executor must pass explicit readiness")

    def factory(data):
        return SecurityAdapter(data, query_executor=executor)

    return factory
