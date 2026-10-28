"""One restricted PostgreSQL connection per query, closed before returning or cancelling."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import math
import threading
from uuid import UUID

from app.adapters.ports import Context
from app.security.sql_policy import QueryPlan, SQL_BY_QUESTION, prepare_query

MAX_ROWS = 100
MAX_RESPONSE_BYTES = 256 * 1024
QUERY_SECONDS = 3


class QueryUnavailable(Exception):
    """A configured restricted connection could not be established or verified."""


class QueryResultLimit(Exception):
    """The complete result exceeds the allowed response size or row count."""


@dataclass(frozen=True)
class ReaderConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False)

    def __post_init__(self):
        if not all(
            isinstance(value, str) and value for value in (self.host, self.dbname, self.user, self.password)
        ):
            raise ValueError("Restricted reader configuration is incomplete")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("Restricted reader port is invalid")


class _RunningQuery:
    def __init__(self):
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.connection = None

    def cancel(self):
        self.stopped.set()
        with self.lock:
            connection = self.connection
        if connection is not None:
            try:
                connection.cancel_safe(timeout=1)
            except Exception:
                pass  # The worker still closes its connection; cancellation never releases results.


def normalize_row(columns, values):
    row = {}
    for name, value in zip(columns, values, strict=True):
        if isinstance(value, UUID):
            value = str(value)
        if value is not None and type(value) not in (str, int, float, bool):
            raise ValueError("Unsupported result type")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Non-finite result")
        row[name] = value
    return row


def verify_login(connection, configured_user):
    # Check effective privileges too: NOINHERIT alone does not prevent SET ROLE or PUBLIC grants.
    role = connection.execute(
        "SELECT session_user, current_user, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, "
        "rolreplication, rolinherit, oid FROM pg_catalog.pg_roles WHERE rolname=session_user"
    ).fetchone()
    if role is None or role[:2] != (configured_user, configured_user) or any(role[2:8]):
        raise QueryUnavailable()
    if connection.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE member=%s) "
        "OR has_database_privilege(current_database(), 'CREATE,TEMPORARY') "
        "OR has_database_privilege(current_database(), 'CONNECT WITH GRANT OPTION') "
        "OR EXISTS(SELECT 1 FROM pg_catalog.pg_namespace "
        " WHERE nspname !~ '^pg_' AND nspname <> 'information_schema' "
        " AND (has_schema_privilege(oid, 'CREATE') OR has_schema_privilege(oid, 'USAGE WITH GRANT OPTION')))",
        (role[8],),
    ).fetchone()[0]:
        raise QueryUnavailable()
    return role[8]


def verify_reader(connection, configured_user):
    role_oid = verify_login(connection, configured_user)
    if connection.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND ("
        " (c.relkind IN ('r','p','v','m','f') AND ("
        "  has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
        "  OR has_any_column_privilege(c.oid,'SELECT WITH GRANT OPTION') "
        "  OR has_any_column_privilege(c.oid,'INSERT,UPDATE,REFERENCES') "
        "  OR ((has_table_privilege(c.oid,'SELECT') OR has_any_column_privilege(c.oid,'SELECT')) "
        "      AND NOT (n.nspname='paperpilot_security' AND c.relname IN ('paper_statistics','paper_topics'))))) "
        " OR (c.relkind='S' AND has_sequence_privilege(c.oid,'USAGE,SELECT,UPDATE')))) "
        "OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        " WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' "
        " AND p.prosecdef AND has_function_privilege(p.oid,'EXECUTE'))"
    ).fetchone()[0]:
        raise QueryUnavailable()
    views = connection.execute(
        "SELECT c.relname FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='paperpilot_security' AND c.relname IN ('paper_statistics','paper_topics') "
        "AND c.relkind='v' AND c.relowner<>%s AND 'security_barrier=true'=ANY(c.reloptions) "
        "AND has_table_privilege(c.oid,'SELECT')",
        (role_oid,),
    ).fetchall()
    if {row[0] for row in views} != {"paper_statistics", "paper_topics"}:
        raise QueryUnavailable()


class PostgresQueryExecutor:
    def __init__(self, readers: Mapping[UUID, ReaderConfig]):
        if not readers or any(
            type(key) is not UUID or type(value) is not ReaderConfig for key, value in readers.items()
        ):
            raise ValueError("Explicit project reader configurations are required")
        # Import only on explicit configuration: the disabled gateway needs no PostgreSQL driver.
        import psycopg

        self._driver = psycopg
        self._readers = dict(readers)

    async def __call__(self, ctx: Context, plan: QueryPlan):
        config = self._readers.get(ctx.project_id)
        if config is None:
            raise QueryUnavailable()
        # Rebuild again at the executor boundary, including a detached parameter dictionary.
        trusted = next(
            (p for sql in SQL_BY_QUESTION.values() if (p := prepare_query(sql, ctx.project_id)) == plan), None
        )
        if type(plan) is not QueryPlan or trusted is None:
            raise ValueError("Unapproved query plan")
        running = _RunningQuery()
        worker = asyncio.create_task(asyncio.to_thread(self._run, ctx, trusted, config, running))
        try:
            async with asyncio.timeout(QUERY_SECONDS):
                return await asyncio.shield(worker)
        except (TimeoutError, asyncio.CancelledError):
            # Never abandon a to_thread worker. Drain it even if cancellation is requested again.
            cleanup = asyncio.create_task(asyncio.to_thread(running.cancel))
            for task in (cleanup, worker):
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not task.cancelled():
                    task.exception()
            raise

    def _run(self, ctx, plan, config, running):
        try:
            connection = self._driver.connect(
                host=config.host,
                port=config.port,
                dbname=config.dbname,
                user=config.user,
                password=config.password,
                connect_timeout=QUERY_SECONDS,
                application_name=f"paperpilot-security-{ctx.request_id}",
                options="-c default_transaction_read_only=on -c statement_timeout=3000 "
                "-c lock_timeout=1000 -c idle_in_transaction_session_timeout=3000 -c search_path=pg_catalog",
                autocommit=True,
            )
        except self._driver.Error:
            raise QueryUnavailable() from None
        with running.lock:
            running.connection = connection
        try:
            if running.stopped.is_set():
                raise TimeoutError()
            connection.execute("BEGIN READ ONLY")
            verify_reader(connection, config.user)
            if running.stopped.is_set():
                raise TimeoutError()
            with connection.cursor(name="paperpilot_statistics") as cursor:
                cursor.execute(plan.sql, plan.parameters)
                columns = [column.name for column in cursor.description]
                if columns not in (["year", "count"], ["topic", "count"], ["paper_id", "title"]):
                    raise ValueError("Unapproved result columns")
                rows, size = [], 0
                while (values := cursor.fetchone()) is not None:
                    if running.stopped.is_set():
                        raise TimeoutError()
                    row = normalize_row(columns, values)
                    size += len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                    if len(rows) == MAX_ROWS or size > MAX_RESPONSE_BYTES:
                        raise QueryResultLimit()
                    rows.append(row)
            connection.rollback()
            return columns, rows
        except (self._driver.errors.QueryCanceled, self._driver.errors.LockNotAvailable):
            raise TimeoutError() from None
        finally:
            with running.lock:
                running.connection = None
            # Closing discards any failed transaction/session; no connection is pooled or reused.
            connection.close()
