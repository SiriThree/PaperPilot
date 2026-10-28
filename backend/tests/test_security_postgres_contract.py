"""Opt-in A/D view contract tests against A's pinned migration in a disposable PG16 container."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.adapters.mock_ai import MockAI
from app.adapters.ports import Context, FileReference
from app.schemas import ProjectCreate, QuestionRequest
from app.errors import AppError
from app.security import SecurityAdapter, build_security
from app.security.sql_policy import SQL_BY_QUESTION, prepare_query

A_COMMIT = "0b2953c218668af71a4bf869e5c6d238cc0d588d"
IMAGE = "pgvector/pgvector@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a"
pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        os.getenv("PAPERPILOT_SECURITY_POSTGRES_TESTS") != "1",
        reason="D PostgreSQL contract requires explicit opt-in, Docker and A's pinned reference checkout",
    ),
]


@pytest.fixture(scope="module")
def pg_contract(record_testsuite_property):
    import app
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    root = Path(__file__).resolve().parents[2]
    source = Path(os.environ["PAPERPILOT_SECURITY_TEST_A_SOURCE"]).resolve()
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if head != A_COMMIT:
        pytest.fail("A reference must match the documented commit")
    subprocess.run(
        ["git", "-C", str(source), "diff", "--exit-code", "HEAD", "--", "backend/app/data"], check=True
    )
    old_paths = list(app.__path__)
    old_modules = {
        key: value for key, value in sys.modules.items() if key == "app.data" or key.startswith("app.data.")
    }
    old_data_module = getattr(app, "data", None)
    app.__path__ = old_paths + [str(source / "backend/app")]
    database = None
    container = None
    try:
        from app.data.adapter import PostgresData
        from app.data.database import ConnectionConfig, PostgresDatabase
        from app.data.settings import DataSettings

        name = "paperpilot-d-step2-" + uuid4().hex[:12]
        environment = dict(os.environ, POSTGRES_PASSWORD=secrets.token_urlsafe(32))
        result = subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                name,
                "--publish",
                "127.0.0.1::5432",
                "--env",
                "POSTGRES_PASSWORD",
                "--env",
                "POSTGRES_DB=paperpilot_d_step2",
                IMAGE,
            ],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        container = result.stdout.strip()
        if len(container) != 64 or any(c not in "0123456789abcdef" for c in container):
            pytest.fail("Docker did not return a container identity")
        binding = subprocess.run(
            ["docker", "port", container, "5432/tcp"], check=True, capture_output=True, text=True
        ).stdout.strip()
        config = ConnectionConfig(
            host="127.0.0.1",
            port=int(binding.rsplit(":", 1)[1]),
            user="postgres",
            password=environment["POSTGRES_PASSWORD"],
            dbname="paperpilot_d_step2",
            connect_timeout=1,
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(**config.conninfo(), autocommit=True) as ready:
                    assert 160000 <= ready.info.server_version < 170000
                    record_testsuite_property("security_postgres_server_version", ready.info.server_version)
                    record_testsuite_property("security_postgres_a_commit", A_COMMIT)
                    record_testsuite_property("security_postgres_image", IMAGE)
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    pytest.fail("Dedicated PostgreSQL test container did not become ready")
                time.sleep(0.2)
        database = PostgresDatabase(config)
        database.migrate()
        database.seed_development_users()
        data = PostgresData(database, DataSettings())
        user = data.user_by_name("alice")
        contexts = []
        for label in ("A", "B"):
            project = data.create_project(user.user_id, ProjectCreate(name=f"D contract {label}"), uuid4())
            contexts.append(Context(user.user_id, project.project_id, uuid4()))

        def paper(ctx, title, year):
            paper_id = uuid4()
            result, _ = data.import_paper(
                ctx,
                title,
                year,
                hashlib.sha256(str(paper_id).encode()).hexdigest(),
                FileReference(f"{paper_id}.pdf", paper_id, ctx.project_id),
                uuid4().hex,
            )
            return result

        first = paper(contexts[0], "two topics, evidence exists", 2024)
        missing = paper(contexts[0], "no topic, no evidence", None)
        removed = paper(contexts[0], "removed", 2023)
        other = paper(contexts[1], "other project private title", 2025)
        ai = MockAI(data)
        while (job := data.claim_job()) is not None:
            data.complete_job(
                job, asyncio.run(ai.process(job.context, job.task.task_id, job.payload["file"]))
            )
        data.create_task(
            contexts[0],
            "evidence",
            {"question": QuestionRequest(question="contract evidence"), "paper_ids": [first.paper_id]},
            uuid4().hex,
        )
        job = data.claim_job()
        # A saved all-insufficient result still counts as evidence existence, not evidence quality.
        data.complete_job(job, asyncio.run(ai.extract(contexts[0], first.paper_id, [])))
        data.remove_paper(contexts[0], removed.paper_id)

        roles = ["d_contract_" + uuid4().hex[:12] for _ in range(3)]
        passwords = [secrets.token_urlsafe(32) for _ in roles]
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            for project_index, title, paper_ids in [
                (0, "rag", [first.paper_id]),
                (0, "security", [first.paper_id, removed.paper_id]),
                (1, "security", [other.paper_id]),
            ]:
                topic_id = uuid4()
                project_id = contexts[project_index].project_id
                admin.execute("INSERT INTO topics VALUES (%s,%s,%s)", (topic_id, project_id, title))
                for paper_id in paper_ids:
                    admin.execute(
                        "INSERT INTO paper_topics VALUES (%s,%s,%s)", (paper_id, topic_id, project_id)
                    )
            source_sql = (root / "backend/app/security/migrations/001_statistics_views.sql").read_text(
                encoding="utf-8"
            )
            admin.execute(source_sql, prepare=False)
            admin.execute("REVOKE CREATE, TEMPORARY ON DATABASE paperpilot_d_step2 FROM PUBLIC")
            admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            for index, (role, password) in enumerate(zip(roles, passwords)):
                admin.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT "
                        "NOCREATEDB NOCREATEROLE NOREPLICATION"
                    ).format(sql.Identifier(role), sql.Literal(password))
                )
                admin.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA paperpilot_security TO {}").format(sql.Identifier(role))
                )
                admin.execute(
                    sql.SQL(
                        "GRANT SELECT ON paperpilot_security.paper_statistics, "
                        "paperpilot_security.paper_topics TO {}"
                    ).format(sql.Identifier(role))
                )
                if index < 2:
                    admin.execute(
                        "INSERT INTO paperpilot_security.project_readers VALUES (%s,%s)",
                        (role, contexts[index].project_id),
                    )

        def connect(index=0):
            return psycopg.connect(
                **(config.conninfo() | {"user": roles[index], "password": passwords[index]}),
                autocommit=True,
                row_factory=dict_row,
            )

        yield SimpleNamespace(
            data=data,
            config=config,
            contexts=contexts,
            roles=roles,
            connect=connect,
            first=first,
            missing=missing,
            removed=removed,
            other=other,
        )
    finally:
        if database is not None:
            database.close()
        if container is not None:
            subprocess.run(
                ["docker", "stop", "--time", "5", container], check=True, capture_output=True, timeout=30
            )
        app.__path__ = old_paths
        for name in list(sys.modules):
            if name == "app.data" or name.startswith("app.data."):
                del sys.modules[name]
        sys.modules.update(old_modules)
        if old_data_module is None:
            if hasattr(app, "data"):
                delattr(app, "data")
        else:
            app.data = old_data_module


def normalized(rows):
    return sorted(
        json.dumps(
            {key: str(value) if isinstance(value, UUID) else value for key, value in row.items()},
            sort_keys=True,
        )
        for row in rows
    )


@pytest.mark.parametrize(
    "question,intent", tuple(zip(SQL_BY_QUESTION, ("by_year", "by_topic", "missing_evidence")))
)
@pytest.mark.parametrize("index", [0, 1])
def test_plans_match_a_data_port_on_real_schema(pg_contract, question, intent, index):
    ctx = pg_contract.contexts[index]
    columns, expected = pg_contract.data.controlled_statistics(ctx, intent)
    plan = prepare_query(SQL_BY_QUESTION[question], ctx.project_id)
    with pg_contract.connect(index) as reader:
        cursor = reader.execute(plan.sql, plan.parameters)
        assert [column.name for column in cursor.description] == columns
        assert normalized(cursor.fetchall()) == normalized(expected)


def test_multiple_topics_do_not_inflate_years_or_missing_evidence(pg_contract):
    with pg_contract.connect() as reader:
        rows = reader.execute("SELECT * FROM paperpilot_security.paper_statistics").fetchall()
        assert len(rows) == 2
        assert {row["paper_id"] for row in rows} == {pg_contract.first.paper_id, pg_contract.missing.paper_id}
        assert (
            next(row for row in rows if row["paper_id"] == pg_contract.first.paper_id)["has_evidence"] is True
        )
        topics = reader.execute("SELECT * FROM paperpilot_security.paper_topics").fetchall()
        assert len(topics) == 2 and {row["topic"] for row in topics} == {"rag", "security"}


def test_login_scope_survives_spoofed_project_variable_and_parameter(pg_contract):
    other_project = pg_contract.contexts[1].project_id
    with pg_contract.connect() as reader:
        reader.execute("SELECT set_config('paperpilot.project_id', %s, false)", (str(other_project),))
        assert reader.execute(
            "SELECT DISTINCT project_id FROM paperpilot_security.paper_statistics"
        ).fetchall() == [{"project_id": pg_contract.contexts[0].project_id}]
        plan = prepare_query(next(iter(SQL_BY_QUESTION.values())), other_project)
        assert reader.execute(plan.sql, plan.parameters).fetchall() == []


def test_unmapped_login_has_no_visible_rows(pg_contract):
    with pg_contract.connect(2) as reader:
        for view in ("paper_statistics", "paper_topics"):
            assert reader.execute(f"SELECT * FROM paperpilot_security.{view}").fetchall() == []


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM public.papers",
        "SELECT * FROM public.project_papers",
        "SELECT * FROM public.audit_events",
        "SELECT * FROM public.outbox_events",
        "SELECT * FROM paperpilot_security.project_readers",
        "DELETE FROM public.project_papers",
        "DELETE FROM paperpilot_security.paper_statistics",
        "CREATE TABLE public.d_blocked (id int)",
        "CREATE TEMP TABLE d_blocked (id int)",
        "DELETE FROM paperpilot_security.project_readers",
    ],
)
def test_reader_acl_blocks_direct_bypass_and_writes(pg_contract, statement):
    import psycopg

    with pg_contract.connect() as reader:
        assert reader.execute("SHOW default_transaction_read_only").fetchone() == {
            "default_transaction_read_only": "off"
        }
        errors = (psycopg.errors.InsufficientPrivilege,)
        if statement == "DELETE FROM paperpilot_security.paper_statistics":
            assert reader.execute(
                "SELECT has_table_privilege(current_user, 'paperpilot_security.paper_statistics', 'DELETE') AS permitted"
            ).fetchone() == {"permitted": False}
            # PostgreSQL may reject a non-updatable view before checking the absent DELETE grant.
            errors += (psycopg.errors.ObjectNotInPrerequisiteState,)
        with pytest.raises(errors):
            reader.execute(statement)


def test_reader_cannot_assume_other_project_or_admin_identity(pg_contract):
    import psycopg
    from psycopg import sql

    with pg_contract.connect() as reader:
        attributes = reader.execute(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        assert not any(attributes.values())
        for command, role in [("SET ROLE", pg_contract.roles[1]), ("SET SESSION AUTHORIZATION", "postgres")]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                reader.execute(sql.SQL(command + " {}").format(sql.Identifier(role)))


def test_removing_binding_revokes_access_on_existing_connection(pg_contract):
    import psycopg

    role = pg_contract.roles[0]
    with (
        pg_contract.connect() as reader,
        psycopg.connect(**pg_contract.config.conninfo(), autocommit=True) as admin,
    ):
        assert reader.execute(
            "SELECT count(*) AS count FROM paperpilot_security.paper_statistics"
        ).fetchone() == {"count": 2}
        admin.execute("DELETE FROM paperpilot_security.project_readers WHERE login_role=%s", (role,))
        try:
            assert reader.execute(
                "SELECT count(*) AS count FROM paperpilot_security.paper_statistics"
            ).fetchone() == {"count": 0}
        finally:
            admin.execute(
                "INSERT INTO paperpilot_security.project_readers VALUES (%s,%s)",
                (role, pg_contract.contexts[0].project_id),
            )


def test_d_authorization_uses_a_roles_and_keeps_application_execution_closed(pg_contract):
    data = pg_contract.data
    owner = pg_contract.contexts[0]
    security = build_security(data)
    assert security.authorize(owner, "audit.read", "owner") is None
    data.add_member(owner, "bob")
    member = Context(data.user_by_name("bob").user_id, owner.project_id, uuid4())
    try:
        assert security.authorize(member, "project.read", "member") is None
        with pytest.raises(AppError) as error:
            security.authorize(member, "audit.read", "member")
        assert error.value.code == "OWNER_REQUIRED"
        result = asyncio.run(security.execute(member, "按年份统计论文数量"))
        assert (result.decision, result.execution_status) == ("allowed", "not_executed")
        assert not result.rows and not result.columns
        data.remove_member(owner, member.user_id)
        with pytest.raises(AppError) as error:
            security.authorize(member, "project.read", "member")
        assert error.value.code == "RESOURCE_NOT_FOUND"
    finally:
        data.remove_member(owner, member.user_id)


def audit_events(pg_contract, ctx):
    result = pg_contract.data.audit(
        ctx, action=None, decision=None, actor_id=None, since=None, until=None, offset=0, limit=100
    )
    return [event for event in result.items if event.request_id == ctx.request_id]


def test_d_audit_gates_commit_through_a_and_support_event_replay(pg_contract):
    import psycopg

    owner = pg_contract.contexts[0]
    ctx = Context(owner.user_id, owner.project_id, uuid4())

    async def execute(actual_ctx, plan):
        # A separate connection must observe the pre-event before the reader runs any SQL.
        with psycopg.connect(**pg_contract.config.conninfo()) as observer:
            assert observer.execute(
                "SELECT action, execution_status FROM audit_events WHERE request_id=%s", (ctx.request_id,)
            ).fetchall() == [("security.query.prepared", "pending")]
        with pg_contract.connect() as reader:
            cursor = reader.execute(plan.sql, plan.parameters)
            return [column.name for column in cursor.description], cursor.fetchall()

    result = asyncio.run(
        SecurityAdapter(pg_contract.data, query_executor=execute).execute(ctx, "按年份统计论文数量")
    )
    assert result.rows == [{"year": 2024, "count": 1}, {"year": None, "count": 1}]
    recorded = sorted(audit_events(pg_contract, ctx), key=lambda event: event.timestamp)
    assert [event.execution_status for event in recorded] == ["pending", "executed"]
    assert all(not event.mock for event in recorded)
    assert len({event.event_id for event in recorded}) == 2
    pg_contract.data.append_audit(recorded[0])
    assert len(audit_events(pg_contract, ctx)) == 2
    with pytest.raises(AppError) as error:
        pg_contract.data.append_audit(recorded[0].model_copy(update={"action": "changed"}))
    assert error.value.code == "AUDIT_EVENT_CONFLICT"
    assert len(audit_events(pg_contract, ctx)) == 2


@pytest.mark.parametrize("stage", ["prepared", "finished"])
@pytest.mark.parametrize("failure", ["rollback", "lost_ack"])
def test_a_audit_commit_failure_closes_d_gate(pg_contract, monkeypatch, stage, failure):
    data = pg_contract.data
    owner = pg_contract.contexts[0]
    ctx = Context(owner.user_id, owner.project_id, uuid4())
    append = data.append_audit
    attempted = []
    calls = 0

    def before_commit(operation, connection):
        if operation == "append_audit" and attempted[-1].action == f"security.query.{stage}":
            # Fail after INSERT inside A's real transaction, not before append_audit.
            connection.execute("SELECT 1 / 0")

    def audit(event):
        attempted.append(event)
        append(event)
        if failure == "lost_ack" and event.action == f"security.query.{stage}":
            # Simulate lost acknowledgment after a confirmed real commit; no wire-fault claim.
            raise AppError(503, "DATA_COMMIT_UNKNOWN", "PRIVATE_AUDIT_CONNECTION")

    async def execute(actual_ctx, plan):
        nonlocal calls
        calls += 1
        with pg_contract.connect() as reader:
            cursor = reader.execute(plan.sql, plan.parameters)
            return [column.name for column in cursor.description], cursor.fetchall()

    if failure == "rollback":
        monkeypatch.setattr(data, "_before_commit", before_commit)
    monkeypatch.setattr(data, "append_audit", audit)
    with pytest.raises(AppError) as error:
        asyncio.run(SecurityAdapter(data, query_executor=execute).execute(ctx, "按年份统计论文数量"))
    assert (error.value.status, error.value.code) == (503, "AUDIT_UNAVAILABLE")
    assert "PRIVATE" not in error.value.message and "未执行" not in error.value.message
    assert calls == (stage == "finished")
    recorded = audit_events(pg_contract, ctx)
    assert len(recorded) == (stage == "finished") + (failure == "lost_ack")
    assert len(attempted) == 1 + (stage == "finished")
    if failure == "lost_ack":
        # Reconcile the identical event through A's idempotency without rerunning SQL.
        append(attempted[-1])
        assert len(audit_events(pg_contract, ctx)) == len(recorded)
