"""Opt-in attack, resource and cleanup tests of the real restricted execution chain."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
import os
import time
from uuid import uuid4

import pytest

from app.adapters.ports import Context
from app.errors import AppError
from app.security import PostgresQueryExecutor, ReaderConfig, SecurityAdapter, build_security
from app.security.sql_policy import QueryPlan, SQL_BY_QUESTION, prepare_query
from security_contracts.postgres_provider import postgres_workspace
from test_security_gateway import ATTACKS

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        os.getenv("PAPERPILOT_SECURITY_POSTGRES_TESTS") != "1",
        reason="Restricted executor tests require explicit opt-in, Docker and A's pinned reference",
    ),
]


@pytest.fixture(scope="module")
def pg(record_testsuite_property):
    with postgres_workspace(record_testsuite_property) as workspace:
        for owner in workspace.contexts:
            workspace.data.add_member(owner, "bob")
        yield workspace


def context(pg, index=0):
    owner = pg.contexts[index]
    return Context(owner.user_id, owner.project_id, uuid4())


def recorded(pg, ctx):
    owner = next(owner for owner in pg.contexts if owner.project_id == ctx.project_id)
    result = pg.data.audit(
        owner, action=None, decision=None, actor_id=None, since=None, until=None, offset=0, limit=100
    )
    return sorted(
        (event for event in result.items if event.request_id == ctx.request_id),
        key=lambda event: event.timestamp,
    )


def normalized(rows):
    return sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)


@contextmanager
def admin(pg):
    import psycopg

    with psycopg.connect(**pg.config.conninfo(), autocommit=True) as connection:
        yield connection


def assert_closed(pg, ctx):
    with admin(pg) as observer:
        # Closing a socket and removing pg_stat_activity can be separated by a scheduler tick.
        deadline = time.monotonic() + 2
        while observer.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE application_name=%s",
            (f"paperpilot-security-{ctx.request_id}",),
        ).fetchone()[0]:
            assert time.monotonic() < deadline, "Executor left a database session behind"
            time.sleep(0.01)


@pytest.mark.parametrize("candidate", ATTACKS)
def test_attack_is_rejected_in_real_configured_chain(pg, candidate):
    ctx = context(pg)

    async def generate(*args):
        return candidate

    security = SecurityAdapter(
        pg.data, candidate_generator=generate, query_executor=PostgresQueryExecutor(pg.readers)
    )
    result = asyncio.run(security.execute(ctx, "untrusted candidate"))
    assert (result.decision, result.execution_status) == ("rejected", "not_executed")
    assert result.reason.code == "SQL_POLICY_REJECTED" and not result.rows and not result.columns
    events = recorded(pg, ctx)
    assert len(events) == 1 and events[0].action == "security.query.finished"
    assert candidate not in events[0].model_dump_json()
    assert_closed(pg, ctx)


@pytest.mark.parametrize("candidate", ATTACKS)
def test_calling_executor_directly_cannot_bypass_plan_validation(pg, candidate):
    ctx = context(pg)
    with pytest.raises(ValueError):
        asyncio.run(
            PostgresQueryExecutor(pg.readers)(ctx, QueryPlan(candidate, {"project_id": str(ctx.project_id)}))
        )
    assert_closed(pg, ctx)


@pytest.mark.parametrize(
    "question,intent", list(zip(SQL_BY_QUESTION, ("by_year", "by_topic", "missing_evidence")))
)
@pytest.mark.parametrize("variant", ["original", "lower", "spaces", "newline", "tabs", "semicolon", "mixed"])
def test_normal_ast_variants_execute_under_restricted_login(pg, question, intent, variant):
    ctx = context(pg, 1 if variant in ("spaces", "tabs", "mixed") else 0)
    if variant in ("lower", "semicolon", "mixed"):
        ctx = Context(pg.data.user_by_name("bob").user_id, ctx.project_id, ctx.request_id)
    sql = SQL_BY_QUESTION[question]
    candidate = {
        "original": sql,
        "lower": sql.lower(),
        "spaces": "  " + sql.replace(" ", "   ") + "  ",
        "newline": sql.replace(" ", "\n"),
        "tabs": sql.replace(" ", "\t"),
        "semicolon": sql + ";",
        "mixed": sql.replace("SELECT", "SeLeCt"),
    }[variant]

    async def generate(*args):
        return candidate

    result = asyncio.run(
        SecurityAdapter(
            pg.data, candidate_generator=generate, query_executor=PostgresQueryExecutor(pg.readers)
        ).execute(ctx, question)
    )
    columns, rows = pg.data.controlled_statistics(ctx, intent)
    assert (result.decision, result.execution_status) == ("allowed", "executed")
    assert result.columns == columns and normalized(result.rows) == normalized(rows)
    assert result.request_id == ctx.request_id and not result.mock
    assert [event.execution_status for event in recorded(pg, ctx)] == ["pending", "executed"]
    assert_closed(pg, ctx)


def test_admin_login_is_rejected_before_statistics(pg):
    ctx = context(pg)
    config = ReaderConfig(
        pg.config.host, pg.config.port, pg.config.dbname, pg.config.user, pg.config.password
    )
    with pytest.raises(AppError) as error:
        asyncio.run(
            build_security(pg.data, readers={ctx.project_id: config}).execute(
                ctx, next(iter(SQL_BY_QUESTION))
            )
        )
    assert error.value.code == "GATEWAY_UNAVAILABLE"
    assert [event.execution_status for event in recorded(pg, ctx)] == ["pending", "failed"]
    assert_closed(pg, ctx)


@pytest.mark.parametrize(
    "change", ["SUPERUSER", "BYPASSRLS", "CREATEDB", "CREATEROLE", "REPLICATION", "INHERIT"]
)
def test_privileged_role_flags_fail_closed(pg, change):
    from psycopg import sql

    ctx = context(pg)
    with admin(pg) as connection:
        connection.execute(sql.SQL("ALTER ROLE {} " + change).format(sql.Identifier(pg.roles[0])))
        try:
            with pytest.raises(AppError) as error:
                asyncio.run(
                    build_security(pg.data, readers=pg.readers).execute(ctx, next(iter(SQL_BY_QUESTION)))
                )
            assert error.value.code == "GATEWAY_UNAVAILABLE"
        finally:
            connection.execute(sql.SQL("ALTER ROLE {} NO" + change).format(sql.Identifier(pg.roles[0])))
    assert_closed(pg, ctx)


@pytest.mark.parametrize(
    "grant,revoke",
    [
        (
            "GRANT SELECT ON paperpilot_security.paper_statistics TO {} WITH GRANT OPTION",
            "REVOKE GRANT OPTION FOR SELECT ON paperpilot_security.paper_statistics FROM {}",
        ),
        ("GRANT SELECT ON public.papers TO {}", "REVOKE SELECT ON public.papers FROM {}"),
        ("GRANT SELECT(title) ON public.papers TO {}", "REVOKE SELECT(title) ON public.papers FROM {}"),
        ("GRANT INSERT ON public.papers TO {}", "REVOKE INSERT ON public.papers FROM {}"),
        ("GRANT SELECT ON public.audit_events TO {}", "REVOKE SELECT ON public.audit_events FROM {}"),
        ("GRANT SELECT ON public.outbox_events TO {}", "REVOKE SELECT ON public.outbox_events FROM {}"),
        (
            "GRANT SELECT ON paperpilot_security.project_readers TO {}",
            "REVOKE SELECT ON paperpilot_security.project_readers FROM {}",
        ),
        ("GRANT CREATE ON SCHEMA public TO {}", "REVOKE CREATE ON SCHEMA public FROM {}"),
        (
            "GRANT TEMPORARY ON DATABASE paperpilot_d_step2 TO {}",
            "REVOKE TEMPORARY ON DATABASE paperpilot_d_step2 FROM {}",
        ),
        (
            "GRANT UPDATE ON paperpilot_security.paper_statistics TO {}",
            "REVOKE UPDATE ON paperpilot_security.paper_statistics FROM {}",
        ),
    ],
)
def test_effective_permission_drift_fails_closed(pg, grant, revoke):
    from psycopg import sql

    ctx = context(pg)
    with admin(pg) as connection:
        connection.execute(sql.SQL(grant).format(sql.Identifier(pg.roles[0])))
        try:
            with pytest.raises(AppError) as error:
                asyncio.run(
                    build_security(pg.data, readers=pg.readers).execute(ctx, next(iter(SQL_BY_QUESTION)))
                )
            assert error.value.code == "GATEWAY_UNAVAILABLE"
        finally:
            connection.execute(sql.SQL(revoke).format(sql.Identifier(pg.roles[0])))
    assert_closed(pg, ctx)


def test_noinherit_role_membership_is_still_rejected(pg):
    from psycopg import sql

    ctx = context(pg)
    with admin(pg) as connection:
        connection.execute(sql.SQL("GRANT {} TO {}").format(*map(sql.Identifier, pg.roles[1::-1])))
        try:
            with pytest.raises(AppError) as error:
                asyncio.run(
                    build_security(pg.data, readers=pg.readers).execute(ctx, next(iter(SQL_BY_QUESTION)))
                )
            assert error.value.code == "GATEWAY_UNAVAILABLE"
        finally:
            connection.execute(sql.SQL("REVOKE {} FROM {}").format(*map(sql.Identifier, pg.roles[1::-1])))


def test_each_query_has_read_only_transaction_and_server_limits(pg, monkeypatch):
    from app.security import executor as module

    original = module.verify_reader
    checked = []

    def verify(connection, user):
        original(connection, user)
        for setting, expected in [
            ("transaction_read_only", "on"),
            ("statement_timeout", "3s"),
            ("lock_timeout", "1s"),
            ("idle_in_transaction_session_timeout", "3s"),
            ("search_path", "pg_catalog"),
        ]:
            assert connection.execute("SHOW " + setting).fetchone()[0] == expected
        checked.append(connection)

    monkeypatch.setattr(module, "verify_reader", verify)
    for index in (0, 1, 0):
        ctx = context(pg, index)
        result = asyncio.run(
            build_security(pg.data, readers=pg.readers).execute(ctx, next(iter(SQL_BY_QUESTION)))
        )
        assert result.execution_status == "executed"
        assert_closed(pg, ctx)
    assert len(checked) == 3 and all(connection.closed for connection in checked)
    assert checked[0] is not checked[1] and checked[1] is not checked[2]


def test_real_lock_timeout_has_no_rows_and_no_remaining_session(pg):
    import psycopg

    ctx = context(pg)
    with psycopg.connect(**pg.config.conninfo()) as blocker:
        blocker.execute("LOCK TABLE public.project_papers IN ACCESS EXCLUSIVE MODE")
        start = time.monotonic()
        result = asyncio.run(
            build_security(pg.data, readers=pg.readers).execute(ctx, next(iter(SQL_BY_QUESTION)))
        )
        assert result.reason.code == "QUERY_TIMEOUT" and not result.rows and not result.columns
        assert time.monotonic() - start < 4
        assert_closed(pg, ctx)
        blocker.rollback()
    assert [event.execution_status for event in recorded(pg, ctx)] == ["pending", "failed"]


@pytest.mark.parametrize("cancel_count", [1, 3])
def test_cancellation_waits_for_database_session_cleanup(pg, cancel_count):
    import psycopg

    ctx = context(pg)

    async def scenario():
        task = asyncio.create_task(
            build_security(pg.data, readers=pg.readers).execute(ctx, next(iter(SQL_BY_QUESTION)))
        )
        with admin(pg) as observer:
            deadline = time.monotonic() + 2
            while not observer.execute(
                "SELECT 1 FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock'",
                (f"paperpilot-security-{ctx.request_id}",),
            ).fetchone():
                assert time.monotonic() < deadline
                await asyncio.sleep(0.01)
        for _ in range(cancel_count):
            task.cancel()
            await asyncio.sleep(0)
        with pytest.raises(asyncio.CancelledError):
            await task

    with psycopg.connect(**pg.config.conninfo()) as blocker:
        blocker.execute("LOCK TABLE public.project_papers IN ACCESS EXCLUSIVE MODE")
        asyncio.run(scenario())
        assert_closed(pg, ctx)
        blocker.rollback()
    assert [event.execution_status for event in recorded(pg, ctx)] == ["pending", "cancelled"]


@contextmanager
def extra_topics(pg, count, suffix):
    ctx = context(pg)
    identifiers = [uuid4() for _ in range(count)]
    with admin(pg) as connection:
        try:
            for index, identifier in enumerate(identifiers):
                connection.execute(
                    "INSERT INTO public.topics VALUES (%s,%s,%s)",
                    (identifier, ctx.project_id, f"{index:03d}:{suffix}"),
                )
                connection.execute(
                    "INSERT INTO public.paper_topics VALUES (%s,%s,%s)",
                    (pg.first.paper_id, identifier, ctx.project_id),
                )
            yield ctx
        finally:
            connection.execute("DELETE FROM public.paper_topics WHERE topic_id=ANY(%s)", (identifiers,))
            connection.execute("DELETE FROM public.topics WHERE topic_id=ANY(%s)", (identifiers,))


def test_oversized_multibyte_topics_return_no_partial_result(pg):
    with extra_topics(pg, 100, "超" * 1000) as ctx:
        result = asyncio.run(
            build_security(pg.data, readers=pg.readers).execute(ctx, "按研究主题统计论文数量")
        )
        assert result.reason.code == "QUERY_RESULT_TOO_LARGE" and not result.rows and not result.columns
        assert "超" not in "".join(event.model_dump_json() for event in recorded(pg, ctx))
        assert_closed(pg, ctx)


def test_sql_limit_returns_at_most_100_rows(pg):
    with extra_topics(pg, 105, "row-limit") as ctx:
        result = asyncio.run(
            build_security(pg.data, readers=pg.readers).execute(ctx, "按研究主题统计论文数量")
        )
        assert result.execution_status == "executed" and len(result.rows) == 100
        assert len(result.model_dump_json().encode("utf-8")) <= 256 * 1024
        assert_closed(pg, ctx)


def test_server_statement_timeout_stops_a_slow_approved_view(pg, monkeypatch):
    from app.security import executor as module

    ctx = context(pg)
    # Lengthen only the client deadline so this test proves the server's fixed 3-second limit.
    monkeypatch.setattr(module, "QUERY_SECONDS", 10)
    with admin(pg) as connection:
        definition = (
            connection.execute("SELECT pg_get_viewdef('paperpilot_security.paper_statistics'::regclass)")
            .fetchone()[0]
            .rstrip()
            .rstrip(";")
        )
        connection.execute(
            "CREATE OR REPLACE VIEW paperpilot_security.paper_statistics WITH (security_barrier=true) AS SELECT existing.* FROM ("
            + definition
            + ") existing CROSS JOIN (SELECT pg_sleep(10)) delay"
        )
        try:
            start = time.monotonic()
            result = asyncio.run(
                build_security(pg.data, readers=pg.readers).execute(ctx, next(iter(SQL_BY_QUESTION)))
            )
            assert result.reason.code == "QUERY_TIMEOUT" and not result.rows
            assert 2.5 <= time.monotonic() - start < 5
            assert_closed(pg, ctx)
        finally:
            connection.execute(
                "CREATE OR REPLACE VIEW paperpilot_security.paper_statistics WITH (security_barrier=true) AS "
                + definition
            )


@pytest.mark.parametrize("object_type", ["table", "sequence", "definer_function"])
def test_other_schema_and_definer_permissions_are_rejected(pg, object_type):
    from psycopg import sql

    ctx = context(pg)
    # pgx is an ordinary schema, not a pg_ system schema.
    with admin(pg) as connection:
        connection.execute("CREATE SCHEMA pgx_private")
        try:
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA pgx_private TO {}").format(sql.Identifier(pg.roles[0]))
            )
            if object_type == "table":
                connection.execute("CREATE TABLE pgx_private.private_data (secret text)")
                statement = "GRANT SELECT ON pgx_private.private_data TO {}"
            elif object_type == "sequence":
                connection.execute("CREATE SEQUENCE pgx_private.private_sequence")
                statement = "GRANT USAGE ON SEQUENCE pgx_private.private_sequence TO {}"
            else:
                connection.execute(
                    "CREATE FUNCTION pgx_private.private_function() RETURNS int LANGUAGE SQL SECURITY DEFINER AS 'SELECT 1'"
                )
                statement = "GRANT EXECUTE ON FUNCTION pgx_private.private_function() TO {}"
            connection.execute(sql.SQL(statement).format(sql.Identifier(pg.roles[0])))
            with pytest.raises(AppError) as error:
                asyncio.run(
                    build_security(pg.data, readers=pg.readers).execute(ctx, next(iter(SQL_BY_QUESTION)))
                )
            assert error.value.code == "GATEWAY_UNAVAILABLE"
        finally:
            connection.execute("DROP SCHEMA pgx_private CASCADE")
    assert_closed(pg, ctx)


def test_unknown_project_reader_and_bad_password_fail_without_fallback(pg):
    ctx = context(pg)
    for configs in (
        {pg.contexts[1].project_id: pg.readers[pg.contexts[1].project_id]},
        {ctx.project_id: replace(pg.readers[ctx.project_id], password="bad-password")},
    ):
        with pytest.raises(AppError) as error:
            asyncio.run(build_security(pg.data, readers=configs).execute(ctx, next(iter(SQL_BY_QUESTION))))
        assert error.value.code == "GATEWAY_UNAVAILABLE"
        assert_closed(pg, ctx)


def test_project_parameter_tampering_is_rejected_at_executor_boundary(pg):
    ctx = context(pg)
    plan = prepare_query(next(iter(SQL_BY_QUESTION.values())), ctx.project_id)
    plan.parameters["project_id"] = str(pg.contexts[1].project_id)
    with pytest.raises(ValueError):
        asyncio.run(PostgresQueryExecutor(pg.readers)(ctx, plan))
    assert_closed(pg, ctx)


@pytest.mark.parametrize("stage", ["prepared", "finished"])
@pytest.mark.parametrize("committed", [False, True])
def test_critical_audit_failure_gates_the_actual_executor(pg, monkeypatch, stage, committed):
    ctx = context(pg)
    executor = PostgresQueryExecutor(pg.readers)
    append, run = pg.data.append_audit, executor._run
    calls = []

    def observe(*args):
        calls.append(True)
        return run(*args)

    def audit(event):
        if event.action == f"security.query.{stage}":
            if committed:
                append(event)
            raise RuntimeError("PRIVATE_AUDIT_PASSWORD")
        append(event)

    monkeypatch.setattr(executor, "_run", observe)
    monkeypatch.setattr(pg.data, "append_audit", audit)
    with pytest.raises(AppError) as error:
        asyncio.run(
            SecurityAdapter(pg.data, query_executor=executor).execute(ctx, next(iter(SQL_BY_QUESTION)))
        )
    assert error.value.code == "AUDIT_UNAVAILABLE" and "PRIVATE" not in error.value.message
    assert len(calls) == (stage == "finished")
    assert_closed(pg, ctx)


def test_database_disconnect_is_a_safe_failure_and_next_query_uses_fresh_connection(pg, monkeypatch):
    from app.security import executor as module

    original = module.verify_reader

    def terminate(connection, user):
        original(connection, user)
        with admin(pg) as observer:
            assert observer.execute(
                "SELECT pg_terminate_backend(%s)", (connection.info.backend_pid,)
            ).fetchone()[0]

    ctx = context(pg)
    security = build_security(pg.data, readers=pg.readers)
    with monkeypatch.context() as patch:
        patch.setattr(module, "verify_reader", terminate)
        result = asyncio.run(security.execute(ctx, next(iter(SQL_BY_QUESTION))))
        assert result.reason.code == "QUERY_EXECUTION_FAILED" and not result.rows and not result.columns
        assert_closed(pg, ctx)
    result = asyncio.run(security.execute(context(pg), next(iter(SQL_BY_QUESTION))))
    assert result.execution_status == "executed"


def test_wrong_project_login_cannot_return_other_project_rows(pg):
    ctx = context(pg)
    result = asyncio.run(
        build_security(pg.data, readers={ctx.project_id: pg.readers[pg.contexts[1].project_id]}).execute(
            ctx, "查询尚未完成证据提取的论文"
        )
    )
    assert result.execution_status == "executed" and result.rows == []
    assert_closed(pg, ctx)


def test_concurrent_projects_never_share_connections_or_results(pg):
    async def scenario():
        security = build_security(pg.data, readers=pg.readers)
        contexts = [context(pg, index) for index in (0, 1, 0, 1)]
        results = await asyncio.gather(
            *(security.execute(ctx, "查询尚未完成证据提取的论文") for ctx in contexts)
        )
        for ctx, result in zip(contexts, results, strict=True):
            _, expected = pg.data.controlled_statistics(ctx, "missing_evidence")
            assert result.execution_status == "executed" and normalized(result.rows) == normalized(expected)
            assert_closed(pg, ctx)

    asyncio.run(scenario())
