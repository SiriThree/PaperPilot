"""Opt-in disposable PostgreSQL, with dedicated constrained test roles only."""

import asyncio
import hashlib
import os
import secrets
import socket
import subprocess
import sys
import time
import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import httpx
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from psycopg import sql

from app.adapters.ports import Context, FileReference
from app.config import Settings
from app.data.binding import compose_data_binding
from app.data.settings import DataSettings
from app.dev_composition import create_app as create_dev_app
from app.main import create_app
from app.schemas import ProjectCreate, SecureQueryResult
from app.security.adapter import SecurityAdapter
from app.security.restricted_executor import (
    RestrictedConnection,
    RestrictedExecutor,
    candidate_security_factory,
)
from app.security.view_baseline import VIEW_SHA256
from test_phase5a_persistent import WEB, database as isolated_database  # noqa: F401

pytestmark = pytest.mark.real_database


@pytest.fixture(autouse=True)
def compatible_windows_loop():
    if sys.platform != "win32":
        yield
        return
    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(previous)


@pytest.fixture
def restricted(isolated_database):  # noqa: F811
    config, storage = isolated_database
    binding = compose_data_binding(config, DataSettings())
    data = binding.data
    alice = data.user_by_name("alice")
    ctx = []
    for i in range(2):
        p = data.create_project(alice.user_id, ProjectCreate(name=f"Candidate project {i}"), uuid4())
        context = Context(alice.user_id, p.project_id, uuid4())
        paper = uuid4()
        data.import_paper(
            context,
            f"paper {i}",
            2020 + i,
            str(i + 1) * 64,
            FileReference(f"{paper}.pdf", paper, p.project_id),
            uuid4().hex,
        )
        ctx.append(context)
    role = "reader_" + uuid4().hex[:12]
    owner_role = "owner_" + uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        migration = (
            Path(__file__).resolve().parents[1] / "app/security/migrations/001_statistics_views.sql"
        ).read_text(encoding="utf-8")
        admin.execute(migration, prepare=False)
        verifier = (
            Path(__file__).resolve().parents[1] / "app/security/migrations/002_binding_verifier.sql"
        ).read_text(encoding="utf-8")
        admin.execute(verifier, prepare=False)
        admin.execute(
            sql.SQL("REVOKE CREATE, TEMPORARY ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(config.dbname)
            )
        )
        admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(owner_role))
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public, paperpilot_security TO {}").format(
                sql.Identifier(owner_role)
            )
        )
        admin.execute(
            sql.SQL(
                "GRANT SELECT ON public.project_papers, public.papers, public.task_results, public.paper_topics, "
                "public.topics, paperpilot_security.project_readers TO {}"
            ).format(sql.Identifier(owner_role))
        )
        for view in ("paper_statistics", "paper_topics"):
            admin.execute(
                sql.SQL("ALTER VIEW paperpilot_security.{} OWNER TO {}").format(
                    sql.Identifier(view), sql.Identifier(owner_role)
                )
            )
        admin.execute(
            sql.SQL("ALTER FUNCTION paperpilot_security.binding_matches(uuid) OWNER TO {}").format(
                sql.Identifier(owner_role)
            )
        )
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
        admin.execute(sql.SQL("GRANT USAGE ON SCHEMA paperpilot_security TO {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL(
                "GRANT SELECT ON paperpilot_security.paper_statistics, paperpilot_security.paper_topics TO {}"
            ).format(sql.Identifier(role))
        )
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_security.binding_matches(uuid) TO {}").format(
                sql.Identifier(role)
            )
        )
        admin.execute(
            "INSERT INTO paperpilot_security.project_readers VALUES (%s,%s)", (role, ctx[0].project_id)
        )
        # Verify the test binding before candidate SQL is allowed to run.
        assert (
            admin.execute(
                "SELECT project_id FROM paperpilot_security.project_readers WHERE login_role=%s", (role,)
            ).fetchone()[0]
            == ctx[0].project_id
        )
        rows = admin.execute(
            "SELECT c.relname, pg_get_userbyid(c.relowner), pg_get_viewdef(c.oid, false) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='paperpilot_security' AND c.relname IN ('paper_statistics','paper_topics')"
        ).fetchall()
        definitions = {
            name: hashlib.sha256(definition.encode("utf-8")).hexdigest() for name, _, definition in rows
        }
        assert definitions == VIEW_SHA256
        assert len(rows) == 2 and len({owner for _, owner, _ in rows}) == 1
        owner = rows[0][1]
    executor = RestrictedExecutor(
        RestrictedConnection(
            ctx[0].project_id,
            config.host,
            config.port,
            config.dbname,
            role,
            password,
            owner,
            definitions["paper_statistics"],
            definitions["paper_topics"],
        )
    )
    try:
        assert asyncio.run(executor.ready())
        yield config, storage, binding, ctx, executor
    finally:
        executor.close()
        binding.close()


def test_restricted_query_and_audit_via_business_api(restricted):
    config, storage, binding, contexts, executor = restricted
    app = create_app(
        Settings(environment="test", a_mode="persistent", d_mode="real", storage_dir=storage),
        data_binding=binding,
        security_factory=candidate_security_factory(executor),
    )
    with TestClient(app, headers=WEB) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        base = f"/api/v1/projects/{contexts[0].project_id}"
        response = client.post(base + "/secure-queries", json={"question": "按年份统计论文数量"})
        assert response.status_code == 200
        result = response.json()
        assert (result["decision"], result["execution_status"]) == ("allowed", "executed")
        assert result["rows"] == [{"year": 2020, "count": 1}]
        events = client.get(base + "/audit-events").json()["items"]
        assert {e["action"] for e in events if e["request_id"] == result["request_id"]} >= {
            "security.query.prepared",
            "security.query.finished",
        }
        other = client.post(
            f"/api/v1/projects/{contexts[1].project_id}/secure-queries",
            json={"question": "按年份统计论文数量"},
        ).json()
        assert other["execution_status"] == "failed" and not other["rows"]
    restarted = create_dev_app(
        Settings(environment="test", a_mode="persistent", d_mode="real", storage_dir=storage)
    )
    with TestClient(restarted, headers=WEB) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        events = client.get(base + "/audit-events").json()["items"]
        assert any(
            e["request_id"] == result["request_id"] and e["action"] == "security.query.finished"
            for e in events
        )
    with psycopg.connect(
        host=config.host,
        port=config.port,
        dbname=config.dbname,
        user=executor.config.user,
        password=executor.config.password,
    ) as reader:
        visible = reader.execute("SELECT project_id FROM paperpilot_security.paper_statistics").fetchall()
        assert visible == [(contexts[0].project_id,)]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            reader.execute("SELECT * FROM public.papers")


def test_administrator_identity_is_rejected(isolated_database):  # noqa: F811
    config, _ = isolated_database
    executor = RestrictedExecutor(
        RestrictedConnection(
            uuid4(),
            config.host,
            config.port,
            config.dbname,
            config.user,
            config.password,
            "fixture_owner",
            VIEW_SHA256["paper_statistics"],
            VIEW_SHA256["paper_topics"],
        )
    )
    with pytest.raises(PermissionError):
        asyncio.run(executor.ready())


def test_changed_view_definition_is_rejected(restricted):
    config, _, _, _, executor = restricted
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        definition = (
            admin.execute("SELECT pg_get_viewdef('paperpilot_security.paper_statistics'::regclass, false)")
            .fetchone()[0]
            .rstrip(";\n ")
        )
        admin.execute(
            "CREATE OR REPLACE VIEW paperpilot_security.paper_statistics WITH (security_barrier=true) AS "
            "SELECT * FROM (" + definition + ") altered WHERE false"
        )
    with pytest.raises(PermissionError, match="view verification"):
        asyncio.run(executor.ready())
    assert not executor._ready


def test_untrusted_view_owner_is_rejected(restricted):
    from dataclasses import replace

    _, _, _, _, executor = restricted
    candidate = RestrictedExecutor(replace(executor.config, view_owner="unexpected_owner"))
    with pytest.raises(PermissionError, match="owner verification"):
        asyncio.run(candidate.ready())


def test_reader_cannot_own_security_view(restricted):
    config, _, _, _, executor = restricted
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("ALTER VIEW paperpilot_security.paper_statistics OWNER TO {}").format(
                sql.Identifier(executor.config.user)
            )
        )
    with pytest.raises(PermissionError, match="view verification"):
        asyncio.run(executor.ready())


def test_reader_with_base_table_grant_is_rejected(restricted):
    config, _, _, _, executor = restricted
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT SELECT ON public.papers TO {}").format(sql.Identifier(executor.config.user))
        )
    with pytest.raises(PermissionError, match="forbidden table privileges"):
        asyncio.run(executor.ready())


def test_binding_exposing_other_project_is_rejected(restricted):
    config, _, _, contexts, executor = restricted
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "UPDATE paperpilot_security.project_readers SET project_id=%s WHERE login_role=%s",
            (contexts[1].project_id, executor.config.user),
        )
    with pytest.raises(PermissionError, match="another project"):
        asyncio.run(executor.ready())
    assert not executor._ready


def test_empty_project_binding_is_verified_and_missing_or_wrong_binding_refused(restricted):
    config, _, binding, contexts, executor = restricted
    for ctx in contexts:
        for paper in binding.data.papers(ctx):
            binding.data.remove_paper(ctx, paper.paper_id)
    assert asyncio.run(executor.ready())
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "DELETE FROM paperpilot_security.project_readers WHERE login_role=%s", (executor.config.user,)
        )
    with pytest.raises(PermissionError, match="binding mismatch"):
        asyncio.run(executor.ready())
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "INSERT INTO paperpilot_security.project_readers VALUES (%s,%s)",
            (executor.config.user, contexts[1].project_id),
        )
    with pytest.raises(PermissionError, match="binding mismatch"):
        asyncio.run(executor.ready())


def test_public_binding_verifier_grant_is_rejected(restricted):
    config, _, _, _, executor = restricted
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("GRANT EXECUTE ON FUNCTION paperpilot_security.binding_matches(uuid) TO PUBLIC")
    with pytest.raises(PermissionError, match="verifier verification"):
        asyncio.run(executor.ready())


def test_view_security_invoker_option_is_rejected(restricted):
    config, _, _, _, executor = restricted
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("ALTER VIEW paperpilot_security.paper_statistics SET (security_invoker=true)")
    with pytest.raises(PermissionError, match="view verification"):
        asyncio.run(executor.ready())


def test_owner_with_business_write_privilege_is_rejected(restricted):
    config, _, _, _, executor = restricted
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT UPDATE ON public.papers TO {}").format(sql.Identifier(executor.config.view_owner))
        )
    with pytest.raises(PermissionError, match="owner has forbidden permissions"):
        asyncio.run(executor.ready())


def test_unprivileged_sequence_does_not_break_preflight(restricted):
    config, _, _, _, executor = restricted
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("CREATE SEQUENCE public.phase62_unprivileged")
    assert asyncio.run(executor.ready())


@pytest.mark.parametrize("target", ["reader", "owner"])
def test_security_schema_create_privilege_is_rejected(restricted, target):
    config, _, _, _, executor = restricted
    role = executor.config.user if target == "reader" else executor.config.view_owner
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT CREATE ON SCHEMA paperpilot_security TO {}").format(sql.Identifier(role))
        )
    with pytest.raises(PermissionError):
        asyncio.run(executor.ready())


def test_column_write_grant_on_allowed_view_is_rejected(restricted):
    config, _, _, _, executor = restricted
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT UPDATE (year) ON paperpilot_security.paper_statistics TO {}").format(
                sql.Identifier(executor.config.user)
            )
        )
    with pytest.raises(PermissionError, match="view verification"):
        asyncio.run(executor.ready())


@pytest.mark.parametrize("target", ["reader", "owner"])
def test_unexpected_sequence_privilege_is_rejected(restricted, target):
    config, _, _, _, executor = restricted
    role = executor.config.user if target == "reader" else executor.config.view_owner
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("CREATE SEQUENCE public.phase62_unexpected")
        admin.execute(
            sql.SQL("GRANT USAGE ON SEQUENCE public.phase62_unexpected TO {}").format(sql.Identifier(role))
        )
    with pytest.raises(PermissionError, match="sequence privileges"):
        asyncio.run(executor.ready())


def test_unexpected_security_definer_privilege_is_rejected(restricted):
    config, _, _, _, executor = restricted
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "CREATE FUNCTION public.phase62_unexpected() RETURNS int LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$"
        )
    with pytest.raises(PermissionError, match="security definer function privileges"):
        asyncio.run(executor.ready())


@pytest.mark.parametrize("target", ["reader", "owner"])
def test_server_file_function_grant_is_rejected(restricted, target):
    config, _, _, _, executor = restricted
    role = executor.config.user if target == "reader" else executor.config.view_owner
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION pg_catalog.pg_read_file(text) TO {}").format(
                sql.Identifier(role)
            )
        )
    with pytest.raises(PermissionError, match="server file function privileges"):
        asyncio.run(executor.ready())


def test_binding_table_losing_primary_key_is_rejected(restricted):
    config, _, _, _, executor = restricted
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("ALTER TABLE paperpilot_security.project_readers DROP CONSTRAINT project_readers_pkey")
    with pytest.raises(PermissionError, match="Binding table integrity"):
        asyncio.run(executor.ready())


def test_binding_verifier_modified_body_is_rejected(restricted):
    config, _, _, _, executor = restricted
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "CREATE OR REPLACE FUNCTION paperpilot_security.binding_matches(expected_project uuid) "
            "RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$ SELECT true $$"
        )
    with pytest.raises(PermissionError, match="verifier verification"):
        asyncio.run(executor.ready())


@pytest.mark.parametrize("target", ["reader", "owner"])
def test_inheritable_role_is_rejected(restricted, target):
    config, _, _, _, executor = restricted
    role = executor.config.user if target == "reader" else executor.config.view_owner
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(sql.SQL("ALTER ROLE {} INHERIT").format(sql.Identifier(role)))
    with pytest.raises(PermissionError):
        asyncio.run(executor.ready())


def test_reader_with_role_membership_is_rejected(restricted):
    config, _, _, _, executor = restricted
    extra_role = "extra_" + uuid4().hex[:12]
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(extra_role)))
        admin.execute(
            sql.SQL("GRANT {} TO {}").format(sql.Identifier(extra_role), sql.Identifier(executor.config.user))
        )
    with pytest.raises(PermissionError, match="role verification"):
        asyncio.run(executor.ready())


def test_missing_binding_is_rejected_even_when_project_has_no_visible_rows(restricted):
    from dataclasses import replace

    config, _, _, contexts, executor = restricted
    role = "unbound_" + uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
        admin.execute(sql.SQL("GRANT USAGE ON SCHEMA paperpilot_security TO {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL(
                "GRANT SELECT ON paperpilot_security.paper_statistics, paperpilot_security.paper_topics TO {}"
            ).format(sql.Identifier(role))
        )
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_security.binding_matches(uuid) TO {}").format(
                sql.Identifier(role)
            )
        )
    candidate = RestrictedExecutor(
        replace(executor.config, user=role, password=password, project_id=contexts[0].project_id)
    )
    with pytest.raises(PermissionError, match="binding mismatch"):
        asyncio.run(candidate.ready())


def test_correct_empty_project_binding_remains_usable(restricted):
    from dataclasses import replace

    config, _, binding, contexts, executor = restricted
    project = binding.data.create_project(contexts[0].user_id, ProjectCreate(name="Empty project"), uuid4())
    ctx = Context(contexts[0].user_id, project.project_id, uuid4())
    role = "empty_" + uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
        admin.execute(sql.SQL("GRANT USAGE ON SCHEMA paperpilot_security TO {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL(
                "GRANT SELECT ON paperpilot_security.paper_statistics, paperpilot_security.paper_topics TO {}"
            ).format(sql.Identifier(role))
        )
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_security.binding_matches(uuid) TO {}").format(
                sql.Identifier(role)
            )
        )
        admin.execute(
            "INSERT INTO paperpilot_security.project_readers VALUES (%s,%s)", (role, ctx.project_id)
        )
    candidate = RestrictedExecutor(
        replace(executor.config, user=role, password=password, project_id=ctx.project_id)
    )
    assert asyncio.run(candidate.ready())
    result = asyncio.run(
        SecurityAdapter(binding.data, query_executor=candidate).execute(ctx, "按年份统计论文数量")
    )
    assert result.execution_status == "executed" and result.rows == []


def test_all_supported_statistics_preserve_expected_results(restricted):
    _, _, binding, contexts, executor = restricted
    adapter = SecurityAdapter(binding.data, query_executor=executor)
    ctx = contexts[0]
    paper = uuid4()
    binding.data.import_paper(
        ctx,
        "paper 2022",
        2022,
        "f" * 64,
        FileReference(f"{paper}.pdf", paper, ctx.project_id),
        uuid4().hex,
    )
    year = asyncio.run(adapter.execute(ctx, "按年份统计论文数量"))
    topics = asyncio.run(adapter.execute(ctx, "按研究主题统计论文数量"))
    missing = asyncio.run(adapter.execute(ctx, "查询尚未完成证据提取的论文"))
    assert year.execution_status == "executed" and year.rows == [
        {"year": 2020, "count": 1},
        {"year": 2022, "count": 1},
    ]
    assert topics.execution_status == "executed" and topics.columns == ["topic", "count"] and not topics.rows
    assert missing.execution_status == "executed" and missing.columns == ["paper_id", "title"]
    assert len(missing.rows) == 2 and {row["title"] for row in missing.rows} == {"paper 0", "paper 2022"}
    assert [row["paper_id"] for row in missing.rows] == sorted(row["paper_id"] for row in missing.rows)


def test_binding_reassignment_blocks_subsequent_query(restricted):
    config, _, binding, contexts, executor = restricted
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "UPDATE paperpilot_security.project_readers SET project_id=%s WHERE login_role=%s",
            (contexts[1].project_id, executor.config.user),
        )
    result = asyncio.run(
        SecurityAdapter(binding.data, query_executor=executor).execute(contexts[0], "按年份统计论文数量")
    )
    assert result.execution_status == "failed" and not result.rows
    assert not executor._ready


def test_binding_change_between_preflight_and_query_is_not_reported_as_success(restricted, monkeypatch):
    config, _, binding, contexts, executor = restricted
    original = executor._check_role

    async def change_binding_after_check(connection):
        await original(connection)
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "UPDATE paperpilot_security.project_readers SET project_id=%s WHERE login_role=%s",
                (contexts[1].project_id, executor.config.user),
            )

    monkeypatch.setattr(executor, "_check_role", change_binding_after_check)
    result = asyncio.run(
        SecurityAdapter(binding.data, query_executor=executor).execute(contexts[0], "按年份统计论文数量")
    )
    assert result.execution_status == "failed" and not result.rows
    assert not executor._ready


def test_role_search_path_is_overridden_during_readiness_and_execution(restricted, monkeypatch):
    config, _, binding, contexts, executor = restricted
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("ALTER ROLE {} SET search_path TO public, pg_catalog").format(
                sql.Identifier(executor.config.user)
            )
        )
    original = executor._check_role
    observed = []

    async def inspect(connection):
        async with connection.cursor() as cursor:
            await cursor.execute("SHOW search_path")
            observed.append((await cursor.fetchone())[0])
        await original(connection)

    monkeypatch.setattr(executor, "_check_role", inspect)
    assert asyncio.run(executor.ready())
    result = asyncio.run(
        SecurityAdapter(binding.data, query_executor=executor).execute(contexts[0], "按年份统计论文数量")
    )
    assert result.execution_status == "executed" and result.rows == [{"year": 2020, "count": 1}]
    assert observed == ["pg_catalog, public", "pg_catalog, public"]


def test_unreachable_database_never_becomes_ready(restricted):
    from dataclasses import replace

    _, _, _, _, executor = restricted
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        candidate = RestrictedExecutor(replace(executor.config, port=reserved.getsockname()[1]))
        with pytest.raises(psycopg.OperationalError):
            asyncio.run(candidate.ready())
        assert not candidate._ready


def test_two_project_readers_and_duplicate_binding(restricted):
    from dataclasses import replace

    config, _, binding, contexts, executor = restricted
    second_role = "reader_" + uuid4().hex[:12]
    second_password = secrets.token_urlsafe(32)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        with pytest.raises(psycopg.errors.UniqueViolation):
            admin.execute(
                "INSERT INTO paperpilot_security.project_readers VALUES (%s,%s)",
                (executor.config.user, contexts[1].project_id),
            )
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(second_role), sql.Literal(second_password))
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA paperpilot_security TO {}").format(sql.Identifier(second_role))
        )
        admin.execute(
            sql.SQL(
                "GRANT SELECT ON paperpilot_security.paper_statistics, paperpilot_security.paper_topics TO {}"
            ).format(sql.Identifier(second_role))
        )
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_security.binding_matches(uuid) TO {}").format(
                sql.Identifier(second_role)
            )
        )
        admin.execute(
            "INSERT INTO paperpilot_security.project_readers VALUES (%s,%s)",
            (second_role, contexts[1].project_id),
        )
    from app.security.restricted_executor import RestrictedExecutorRouter

    second_config = replace(
        executor.config, project_id=contexts[1].project_id, user=second_role, password=second_password
    )
    router = RestrictedExecutorRouter([executor.config, second_config])
    assert asyncio.run(router.ready())
    adapter = SecurityAdapter(binding.data, query_executor=router)
    result_a = asyncio.run(adapter.execute(contexts[0], "按年份统计论文数量"))
    result_b = asyncio.run(adapter.execute(contexts[1], "按年份统计论文数量"))
    assert result_a.rows == [{"year": 2020, "count": 1}]
    assert result_b.rows == [{"year": 2021, "count": 1}]
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("DELETE FROM paperpilot_security.project_readers WHERE login_role=%s", (second_role,))
    revoked = asyncio.run(adapter.execute(contexts[1], "按年份统计论文数量"))
    assert revoked.execution_status == "failed" and not revoked.rows
    assert not router._ready
    still_a = asyncio.run(adapter.execute(contexts[0], "按年份统计论文数量"))
    assert still_a.rows == [{"year": 2020, "count": 1}]
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "INSERT INTO paperpilot_security.project_readers VALUES (%s,%s)",
            (second_role, contexts[1].project_id),
        )
    router.close()
    second = RestrictedExecutor(second_config)
    assert asyncio.run(second.ready())
    result = asyncio.run(
        SecurityAdapter(binding.data, query_executor=second).execute(contexts[1], "按年份统计论文数量")
    )
    assert result.execution_status == "executed" and result.rows == [{"year": 2021, "count": 1}]
    result = asyncio.run(
        SecurityAdapter(binding.data, query_executor=second).execute(contexts[0], "按年份统计论文数量")
    )
    assert result.execution_status == "failed" and not result.rows


def test_real_database_lock_timeout_and_connection_recovers(restricted):
    config, _, binding, contexts, executor = restricted
    adapter = SecurityAdapter(binding.data, query_executor=executor)
    with psycopg.connect(**config.conninfo()) as admin:
        admin.execute("LOCK TABLE public.project_papers IN ACCESS EXCLUSIVE MODE")
        result = asyncio.run(adapter.execute(contexts[0], "按年份统计论文数量"))
        assert result.execution_status == "failed" and result.reason.code == "QUERY_TIMEOUT"
        assert result.rows == []
        admin.rollback()
    recovered = asyncio.run(adapter.execute(contexts[0], "按年份统计论文数量"))
    assert recovered.execution_status == "executed" and recovered.rows == [{"year": 2020, "count": 1}]


def test_real_database_row_boundary_fails_closed(restricted):
    _, _, binding, contexts, executor = restricted
    data, ctx = binding.data, contexts[0]
    for i in range(99):
        paper_id = uuid4()
        data.import_paper(
            ctx,
            "extra",
            2022,
            f"{i + 1:064x}",
            FileReference(f"{paper_id}.pdf", paper_id, ctx.project_id),
            uuid4().hex,
        )
    result = asyncio.run(
        SecurityAdapter(data, query_executor=executor).execute(ctx, "查询尚未完成证据提取的论文")
    )
    assert result.execution_status == "failed" and result.reason.code == "QUERY_EXECUTION_FAILED"
    assert not result.rows


def test_post_execution_audit_failure_never_returns_result(restricted, monkeypatch):
    _, _, binding, contexts, executor = restricted
    original = binding.data.append_audit
    completed = []

    async def counted(ctx, plan):
        result = await executor(ctx, plan)
        completed.append(True)
        return result

    def fail_finished(event):
        if event.action == "security.query.finished":
            raise RuntimeError("fixture audit failure")
        return original(event)

    monkeypatch.setattr(binding.data, "append_audit", fail_finished)
    from app.errors import AppError

    with pytest.raises(AppError) as exc:
        asyncio.run(
            SecurityAdapter(binding.data, query_executor=counted).execute(contexts[0], "按年份统计论文数量")
        )
    assert exc.value.code == "AUDIT_UNAVAILABLE"
    assert completed == [True]


def test_pre_execution_audit_failure_blocks_query_and_recovers(restricted, monkeypatch):
    _, _, binding, contexts, executor = restricted
    original = binding.data.append_audit
    calls = []

    async def counted(ctx, plan):
        calls.append(True)
        return await executor(ctx, plan)

    def fail_prepared(event):
        if event.action == "security.query.prepared":
            raise RuntimeError("fixture audit failure")
        return original(event)

    from app.errors import AppError

    adapter = SecurityAdapter(binding.data, query_executor=counted)
    monkeypatch.setattr(binding.data, "append_audit", fail_prepared)
    with pytest.raises(AppError) as exc:
        asyncio.run(adapter.execute(contexts[0], "按年份统计论文数量"))
    assert exc.value.code == "AUDIT_UNAVAILABLE"
    assert not calls
    monkeypatch.setattr(binding.data, "append_audit", original)
    result = asyncio.run(adapter.execute(contexts[0], "按年份统计论文数量"))
    assert calls == [True]
    assert result.execution_status == "executed" and result.rows == [{"year": 2020, "count": 1}]


def test_real_postgres_http_response_budget_fails_closed(restricted):
    config, storage, binding, contexts, executor = restricted
    ctx = contexts[0]
    columns = ["topic", "count"]
    # Construct data whose row budget passes but the actual response envelope exceeds 256 KiB.
    for size in range(256 * 1024 - 256, 256 * 1024):
        topics = ["x" * (size // 2), "y" * (size - size // 2)]
        rows = [{"topic": topic, "count": 1} for topic in topics]
        data_size = len(json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False).encode("utf-8"))
        output = SecureQueryResult(
            decision="allowed",
            execution_status="executed",
            columns=columns,
            rows=rows,
            policy_id=SecurityAdapter.policy_id,
            request_id=ctx.request_id,
            mock=False,
        )
        response_size = len(JSONResponse(content=output.model_dump(mode="json")).body)
        if data_size <= 256 * 1024 < response_size:
            break
    else:
        pytest.fail("Could not construct a response that crosses only the final HTTP budget")
    paper = binding.data.papers(ctx)[0]
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        for topic in topics:
            topic_id = uuid4()
            admin.execute(
                "INSERT INTO public.topics(topic_id,project_id,name) VALUES (%s,%s,%s)",
                (topic_id, ctx.project_id, topic),
            )
            admin.execute(
                "INSERT INTO public.paper_topics(paper_id,topic_id,project_id) VALUES (%s,%s,%s)",
                (paper.paper_id, topic_id, ctx.project_id),
            )
    app = create_app(
        Settings(environment="test", a_mode="persistent", d_mode="real", storage_dir=storage),
        data_binding=binding,
        security_factory=candidate_security_factory(executor),
    )
    with TestClient(app, headers=WEB) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        response = client.post(
            f"/api/v1/projects/{ctx.project_id}/secure-queries", json={"question": "按研究主题统计论文数量"}
        )
        assert response.status_code == 503 and response.json()["code"] == "QUERY_RESPONSE_TOO_LARGE", (
            response.status_code,
            response.json().get("reason"),
        )
        assert "rows" not in response.json() and all(topic not in response.text for topic in topics)
        events = client.get(f"/api/v1/projects/{ctx.project_id}/audit-events").json()["items"]
        request_id = response.json()["request_id"]
        assert any(
            event["request_id"] == request_id
            and event["action"] == "security.query.finished"
            and event["execution_status"] == "failed"
            for event in events
        )


def test_candidate_real_server_lifecycle_on_default_platform(restricted):
    config, storage, _, contexts, executor = restricted
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(root / "backend"),
            "PAPERPILOT_ENV": "test",
            "PAPERPILOT_AUTH_MODE": "development",
            "PAPERPILOT_A_MODE": "persistent",
            "PAPERPILOT_C_MODE": "mock",
            "PAPERPILOT_D_MODE": "real",
            "PAPERPILOT_SECURITY_EXECUTION": "candidate",
            "PAPERPILOT_SECURITY_ISOLATED_TEST": "1",
            "PAPERPILOT_STORAGE_DIR": str(storage),
            "PAPERPILOT_SECURITY_DB_PROJECT_ID": str(contexts[0].project_id),
            "PAPERPILOT_SECURITY_DB_HOST": config.host,
            "PAPERPILOT_SECURITY_DB_PORT": str(config.port),
            "PAPERPILOT_SECURITY_DB_DBNAME": config.dbname,
            "PAPERPILOT_SECURITY_DB_USER": executor.config.user,
            "PAPERPILOT_SECURITY_DB_PASSWORD": executor.config.password,
            "PAPERPILOT_SECURITY_DB_VIEW_OWNER": executor.config.view_owner,
            "PAPERPILOT_SECURITY_DB_STATISTICS_SHA256": executor.config.statistics_sha256,
            "PAPERPILOT_SECURITY_DB_TOPICS_SHA256": executor.config.topics_sha256,
        }
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "app.selector_server", "--port", str(port)],
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=WEB, timeout=2) as client:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail("candidate server exited during startup")
                try:
                    health = client.get("/api/v1/health")
                    if health.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("candidate server readiness timed out")
            assert "security_executor_ready" in health.json()["limitations"]
            assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
            response = client.post(
                f"/api/v1/projects/{contexts[0].project_id}/secure-queries",
                json={"question": "按年份统计论文数量"},
            )
            assert response.status_code == 200
            assert response.json()["execution_status"] == "executed"
            assert response.json()["rows"] == [{"year": 2020, "count": 1}]
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
