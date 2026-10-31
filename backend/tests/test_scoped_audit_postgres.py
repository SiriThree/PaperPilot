"""Opt-in checks of the reviewed project-scoped audit view."""

import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from app.adapters.ports import Context
from app.config import Settings
from app.data.authentication import PostgresSessions
from app.data.binding import compose_data_binding
from app.data.scoped_audit import (
    ScopedAuditConfig,
    ScopedAuditReader,
    ScopedAuditRouter,
    VIEW_SHA256,
    verify_scoped_audit,
)
from app.data.settings import DataSettings
from app.errors import AppError
from app.main import create_app
from app.security.adapter import SecurityAdapter
from app.schemas import ProjectCreate
from test_phase5a_persistent import database as isolated_database  # noqa: F401
from test_restricted_executor_postgres import compatible_windows_loop, restricted  # noqa: F401

pytestmark = pytest.mark.real_database


def test_scoped_audit_reviewed_view_definition(isolated_database):  # noqa: F811
    config, _ = isolated_database
    migration = (
        Path(__file__).resolve().parents[1] / "app/security/migrations/003_candidate_scoped_audit.sql"
    ).read_text(encoding="utf-8")
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(migration, prepare=False)
        admin.execute("SET search_path=pg_catalog")
        definition = admin.execute(
            "SELECT pg_get_viewdef('paperpilot_audit.candidate_project_events'::regclass,false)"
        ).fetchone()[0]
        digest = hashlib.sha256(definition.encode("utf-8")).hexdigest()
        assert digest == VIEW_SHA256, digest


@pytest.fixture
def scoped_stack(isolated_database):  # noqa: F811
    config, storage = isolated_database
    binding = compose_data_binding(config, DataSettings())
    alice = binding.data.user_by_name("alice")
    bob = binding.data.user_by_name("bob")
    project = binding.data.create_project(alice.user_id, ProjectCreate(name="scoped-audit"), uuid4())
    other = binding.data.create_project(bob.user_id, ProjectCreate(name="other-audit"), uuid4())
    password = secrets.token_urlsafe(24)
    PostgresSessions(binding.data.database).initialize_password("alice", password)
    PostgresSessions(binding.data.database).initialize_password("bob", password)
    owner = "scope_owner_" + uuid4().hex[:12]
    role = "scope_reader_" + uuid4().hex[:12]
    reader_password = secrets.token_urlsafe(32)
    migration = (
        Path(__file__).resolve().parents[1] / "app/security/migrations/003_candidate_scoped_audit.sql"
    ).read_text(encoding="utf-8")
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(migration, prepare=False)
        admin.execute(
            sql.SQL("REVOKE CREATE, TEMPORARY ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(config.dbname)
            )
        )
        admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public,paperpilot_audit TO {}").format(sql.Identifier(owner))
        )
        admin.execute(sql.SQL("GRANT CREATE ON SCHEMA paperpilot_audit TO {}").format(sql.Identifier(owner)))
        admin.execute(
            sql.SQL(
                "GRANT SELECT (event_id,timestamp,request_id,actor_id,project_id,action,resource_type,decision,execution_status,policy_id,duration_ms,mock,details) ON public.audit_events TO {}"
            ).format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("GRANT SELECT (project_id,user_id,role) ON public.project_members TO {}").format(
                sql.Identifier(owner)
            )
        )
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,disabled) ON public.users TO {}").format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("GRANT SELECT ON paperpilot_audit.candidate_readers TO {}").format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("ALTER VIEW paperpilot_audit.candidate_project_events OWNER TO {}").format(
                sql.Identifier(owner)
            )
        )
        admin.execute(
            sql.SQL(
                "ALTER FUNCTION paperpilot_audit.candidate_binding_matches(uuid,uuid) OWNER TO {}"
            ).format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("REVOKE CREATE ON SCHEMA paperpilot_audit FROM {}").format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(role), sql.Literal(reader_password))
        )
        admin.execute(sql.SQL("GRANT USAGE ON SCHEMA paperpilot_audit TO {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL("GRANT SELECT ON paperpilot_audit.candidate_project_events TO {}").format(
                sql.Identifier(role)
            )
        )
        admin.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION paperpilot_audit.candidate_binding_matches(uuid,uuid) TO {}"
            ).format(sql.Identifier(role))
        )
        admin.execute(
            "INSERT INTO paperpilot_audit.candidate_readers VALUES (%s,%s,%s)",
            (role, project.project_id, alice.user_id),
        )
        ids = [uuid4(), uuid4(), uuid4()]
        for event, project_id in zip(ids, [project.project_id, other.project_id, None], strict=True):
            admin.execute(
                "INSERT INTO public.audit_events (event_id,timestamp,request_id,project_id,action,resource_type,decision,execution_status,duration_ms,mock,event_fingerprint) VALUES (%s,now(),%s,%s,'scope.fixture','fixture','allowed','completed',0,false,%s)",
                (event, uuid4(), project_id, "a" * 64),
            )
    reader = ScopedAuditReader(
        ScopedAuditConfig(
            config.host,
            config.port,
            config.dbname,
            role,
            reader_password,
            alice.user_id,
            project.project_id,
            owner,
            config.user,
        )
    )
    try:
        yield config, storage, binding, alice, bob, project, other, password, reader, ids
    finally:
        binding.close()


def test_scoped_reader_credentials_authorization_and_failure_closed(scoped_stack):
    config, storage, binding, alice, bob, project, other, password, reader, ids = scoped_stack
    with reader._connect() as connection:
        verify_scoped_audit(connection, reader.config)
    assert reader.ready()
    app = create_app(
        Settings(
            environment="test",
            a_mode="persistent",
            auth_mode="credentials",
            scoped_audit="isolated",
            storage_dir=storage,
            allowed_origins=("http://trusted.test",),
        ),
        data_binding=binding,
        scoped_audit_reader=reader,
    )
    with TestClient(app, base_url="http://trusted.test") as client:
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        headers = {"Origin": "http://trusted.test", "X-CSRF-Token": csrf}
        assert (
            client.post(
                "/api/v1/auth/login", headers=headers, json={"username": "alice", "password": password}
            ).status_code
            == 200
        )
        path = f"/api/v1/projects/{project.project_id}/audit-events"
        response = client.get(path, params={"action": "scope.fixture"})
        assert response.status_code == 200
        assert [item["event_id"] for item in response.json()["items"]] == [str(ids[0])]
        assert client.get(f"/api/v1/projects/{other.project_id}/audit-events").status_code == 404
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "DELETE FROM paperpilot_audit.candidate_readers WHERE login_role=%s", (reader.config.user,)
            )
        assert client.get(path).status_code == 503
        assert client.get("/api/v1/health").status_code == 503
        rejected = client.post(
            f"/api/v1/projects/{project.project_id}/secure-queries",
            headers=headers,
            json={"question": "按年份统计论文数量"},
        )
        assert rejected.status_code == 503 and rejected.json()["code"] == "AUDIT_UNAVAILABLE"
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
        assert client.get(path).status_code == 401


def test_multi_user_identity_isolation_and_password_rotation(scoped_stack):
    config, storage, binding, alice, bob, project, other, password, first, ids = scoped_stack
    role = "scope_second_" + uuid4().hex[:12]
    second_password = secrets.token_urlsafe(32)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(role), sql.Literal(second_password))
        )
        admin.execute(sql.SQL("GRANT USAGE ON SCHEMA paperpilot_audit TO {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL("GRANT SELECT ON paperpilot_audit.candidate_project_events TO {}").format(
                sql.Identifier(role)
            )
        )
        admin.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION paperpilot_audit.candidate_binding_matches(uuid,uuid) TO {}"
            ).format(sql.Identifier(role))
        )
        admin.execute(
            "INSERT INTO paperpilot_audit.candidate_readers VALUES (%s,%s,%s)",
            (role, other.project_id, bob.user_id),
        )
    second = replace(
        first.config, user=role, password=second_password, user_id=bob.user_id, project_id=other.project_id
    )
    router = ScopedAuditRouter([first.config, second])
    assert router.ready()
    filters = dict(
        action="scope.fixture", decision=None, actor_id=None, since=None, until=None, offset=0, limit=20
    )
    ctx_a = Context(alice.user_id, project.project_id, uuid4())
    ctx_b = Context(bob.user_id, other.project_id, uuid4())
    assert [event.event_id for event in router.audit(ctx_a, **filters).items] == [ids[0]]
    assert [event.event_id for event in router.audit(ctx_b, **filters).items] == [ids[1]]
    with pytest.raises(AppError) as denied:
        router.audit(Context(alice.user_id, other.project_id, uuid4()), **filters)
    assert denied.value.status == 404

    app = create_app(
        Settings(
            environment="test",
            a_mode="persistent",
            auth_mode="credentials",
            scoped_audit="isolated",
            storage_dir=storage,
            allowed_origins=("http://trusted.test",),
        ),
        data_binding=binding,
        scoped_audit_reader=router,
    )
    with TestClient(app, base_url="http://trusted.test") as client:
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        headers = {"Origin": "http://trusted.test", "X-CSRF-Token": csrf}
        for username, own, event, other_project in [
            ("alice", project.project_id, ids[0], other.project_id),
            ("bob", other.project_id, ids[1], project.project_id),
        ]:
            assert (
                client.post(
                    "/api/v1/auth/login", headers=headers, json={"username": username, "password": password}
                ).status_code
                == 200
            )
            response = client.get(f"/api/v1/projects/{own}/audit-events", params={"action": "scope.fixture"})
            assert response.status_code == 200 and [
                item["event_id"] for item in response.json()["items"]
            ] == [str(event)]
            assert client.get(f"/api/v1/projects/{other_project}/audit-events").status_code == 404
            assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200

    # Rotate the dedicated login in this disposable database; old credentials must fail.
    rotated = secrets.token_urlsafe(32)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(rotated)))
    with pytest.raises(AppError) as unavailable:
        router.ready()
    assert unavailable.value.code == "AUDIT_UNAVAILABLE"
    updated = ScopedAuditRouter([first.config, replace(second, password=rotated)])
    assert updated.ready()
    assert [event.event_id for event in updated.audit(ctx_b, **filters).items] == [ids[1]]
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("DELETE FROM paperpilot_audit.candidate_readers WHERE login_role=%s", (role,))
    with pytest.raises(AppError):
        updated.audit(ctx_b, **filters)
    with pytest.raises(AppError):
        updated.ready_for_project(other.project_id)
    assert updated.ready_for_project(project.project_id)
    with pytest.raises(AppError):
        updated.ready()
    assert [event.event_id for event in updated.audit(ctx_a, **filters).items] == [ids[0]]


@pytest.mark.parametrize(
    "change",
    [
        "wrong_owner",
        "base_read",
        "function",
        "view",
        "owner_password",
        "role_membership",
        "wrong_binding",
        "binding_column_grant",
    ],
)
def test_scoped_reader_rejects_unsafe_configuration(scoped_stack, change):
    config, _, _, _, _, _, other, _, reader, _ = scoped_stack
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        if change == "wrong_owner":
            admin.execute("ALTER VIEW paperpilot_audit.candidate_project_events OWNER TO postgres")
        elif change == "base_read":
            admin.execute(
                sql.SQL("GRANT SELECT ON public.audit_events TO {}").format(
                    sql.Identifier(reader.config.user)
                )
            )
        elif change == "function":
            admin.execute(
                "CREATE OR REPLACE FUNCTION paperpilot_audit.candidate_binding_matches(expected_user uuid,expected_project uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS 'SELECT true'"
            )
        elif change == "view":
            admin.execute(
                "CREATE OR REPLACE VIEW paperpilot_audit.candidate_project_events WITH (security_barrier=true) AS SELECT event_id,timestamp,request_id,actor_id,project_id,action,resource_type,decision,execution_status,policy_id,duration_ms,mock,details FROM public.audit_events"
            )
        elif change == "owner_password":
            admin.execute(
                sql.SQL("GRANT SELECT (password_hash) ON public.users TO {}").format(
                    sql.Identifier(reader.config.owner)
                )
            )
        elif change == "role_membership":
            extra = "scope_extra_" + uuid4().hex[:12]
            admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(extra)))
            admin.execute(
                sql.SQL("GRANT {} TO {}").format(sql.Identifier(extra), sql.Identifier(reader.config.user))
            )
        elif change == "wrong_binding":
            admin.execute(
                "UPDATE paperpilot_audit.candidate_readers SET project_id=%s WHERE login_role=%s",
                (other.project_id, reader.config.user),
            )
        else:
            extra = "scope_writer_" + uuid4().hex[:12]
            admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(extra)))
            admin.execute(
                sql.SQL("GRANT UPDATE (project_id) ON paperpilot_audit.candidate_readers TO {}").format(
                    sql.Identifier(extra)
                )
            )
    with pytest.raises(AppError) as error:
        reader.ready()
    assert getattr(error.value, "code", None) == "AUDIT_UNAVAILABLE"


def test_credentials_scoped_audit_and_real_security_query(scoped_stack, restricted):  # noqa: F811
    config, storage, binding, alice, bob, _, _, password, original, _ = scoped_stack
    _, _, _, contexts, executor = restricted
    project = contexts[0].project_id
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "UPDATE paperpilot_audit.candidate_readers SET project_id=%s WHERE login_role=%s",
            (project, original.config.user),
        )
    reader = ScopedAuditReader(replace(original.config, project_id=project))
    executed_calls = []

    async def tracked_executor(ctx, plan):
        executed_calls.append(ctx.request_id)
        return await executor(ctx, plan)

    settings = Settings(
        environment="test",
        a_mode="persistent",
        auth_mode="credentials",
        d_mode="real",
        security_execution="candidate",
        scoped_audit="isolated",
        storage_dir=storage,
        allowed_origins=("http://trusted.test",),
    )
    app = create_app(
        settings,
        data_binding=binding,
        scoped_audit_reader=reader,
        restricted_executor=executor,
        security_factory=lambda data: SecurityAdapter(data, query_executor=tracked_executor),
    )
    with TestClient(app, base_url="http://trusted.test") as client:
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        headers = {"Origin": "http://trusted.test", "X-CSRF-Token": csrf}
        assert (
            client.post(
                "/api/v1/auth/login", headers=headers, json={"username": "alice", "password": password}
            ).status_code
            == 200
        )
        base = f"/api/v1/projects/{project}"
        query = client.post(
            base + "/secure-queries", headers=headers, json={"question": "按年份统计论文数量"}
        )
        assert query.status_code == 200
        assert query.json()["execution_status"] == "executed"
        assert query.json()["rows"] == [{"year": 2020, "count": 1}]
        request_id = query.json()["request_id"]
        audit = client.get(base + "/audit-events")
        assert audit.status_code == 200
        assert any(event["request_id"] == request_id for event in audit.json()["items"])
        assert (
            client.post(base + "/secure-queries", headers=headers, json={"question": "unsupported"}).json()[
                "decision"
            ]
            == "rejected"
        )
        assert len(executed_calls) == 1
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "DELETE FROM paperpilot_audit.candidate_readers WHERE login_role=%s", (reader.config.user,)
            )
        fault = client.post(
            base + "/secure-queries", headers=headers, json={"question": "按年份统计论文数量"}
        )
        assert fault.status_code == 503 and fault.json()["code"] == "AUDIT_UNAVAILABLE"
        assert len(executed_calls) == 1
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "INSERT INTO paperpilot_audit.candidate_readers VALUES (%s,%s,%s)",
                (reader.config.user, project, alice.user_id),
            )
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
        assert client.get(base + "/audit-events").status_code == 401
        assert (
            client.post(
                "/api/v1/auth/login", headers=headers, json={"username": "bob", "password": password}
            ).status_code
            == 200
        )
        assert client.get(base + "/audit-events").status_code == 404
        with psycopg.connect(**config.conninfo()) as admin:
            admin.execute(
                "INSERT INTO public.project_members(project_id,user_id,role) VALUES(%s,%s,'member')",
                (project, bob.user_id),
            )
        assert client.get(base + "/audit-events").status_code == 403
        with psycopg.connect(**config.conninfo()) as admin:
            admin.execute(
                "DELETE FROM public.project_members WHERE project_id=%s AND user_id=%s",
                (project, bob.user_id),
            )
        assert client.get(base + "/audit-events").status_code == 404


def test_credentials_scoped_audit_and_real_security_browser(scoped_stack, restricted):  # noqa: F811
    config, _, _, alice, bob, _, other, password, original, _ = scoped_stack
    _, storage, _, contexts, executor = restricted
    project = contexts[0].project_id
    business_role = "scope_business_" + uuid4().hex[:12]
    business_password = secrets.token_urlsafe(32)
    audit_owner = "scope_writer_owner_" + uuid4().hex[:12]
    second_role = "scope_browser_reader_" + uuid4().hex[:12]
    second_password = secrets.token_urlsafe(32)
    auth_role = "scope_auth_" + uuid4().hex[:12]
    auth_password = secrets.token_urlsafe(32)
    stats_role = "scope_stats_" + uuid4().hex[:12]
    stats_password = secrets.token_urlsafe(32)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "UPDATE paperpilot_audit.candidate_readers SET project_id=%s WHERE login_role=%s",
            (project, original.config.user),
        )
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(second_role), sql.Literal(second_password))
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA paperpilot_audit TO {}").format(sql.Identifier(second_role))
        )
        admin.execute(
            sql.SQL("GRANT SELECT ON paperpilot_audit.candidate_project_events TO {}").format(
                sql.Identifier(second_role)
            )
        )
        admin.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION paperpilot_audit.candidate_binding_matches(uuid,uuid) TO {}"
            ).format(sql.Identifier(second_role))
        )
        admin.execute(
            "INSERT INTO paperpilot_audit.candidate_readers VALUES (%s,%s,%s)",
            (second_role, other.project_id, bob.user_id),
        )
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(business_role), sql.Literal(business_password))
        )
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(audit_owner))
        )
        for role, secret in ((auth_role, auth_password), (stats_role, stats_password)):
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
                ).format(sql.Identifier(role), sql.Literal(secret))
            )
        admin.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(auth_role)))
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,username,password_hash,disabled) ON public.users TO {}").format(
                sql.Identifier(auth_role)
            )
        )
        admin.execute(
            sql.SQL(
                "GRANT SELECT,INSERT,UPDATE ON public.auth_sessions,public.auth_login_limits TO {}"
            ).format(sql.Identifier(auth_role))
        )
        admin.execute(sql.SQL("GRANT INSERT ON public.audit_events TO {}").format(sql.Identifier(auth_role)))
        from test_authentication_boundary_postgres import grant_business_fixture, install_auth_lock

        lock_owner = install_auth_lock(admin, auth_role)
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA paperpilot_security TO {}").format(sql.Identifier(stats_role))
        )
        admin.execute(
            sql.SQL(
                "GRANT SELECT ON paperpilot_security.paper_statistics,paperpilot_security.paper_topics TO {}"
            ).format(sql.Identifier(stats_role))
        )
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_security.binding_matches(uuid) TO {}").format(
                sql.Identifier(stats_role)
            )
        )
        admin.execute(
            "INSERT INTO paperpilot_security.project_readers VALUES (%s,%s)", (stats_role, other.project_id)
        )
        admin.execute(
            sql.SQL("GRANT SELECT ON public.audit_events TO {}").format(sql.Identifier(audit_owner))
        )
        admin.execute(
            sql.SQL("GRANT USAGE,CREATE ON SCHEMA paperpilot_audit TO {}").format(sql.Identifier(audit_owner))
        )
        admin.execute(
            sql.SQL("ALTER VIEW paperpilot_audit.project_events OWNER TO {}").format(
                sql.Identifier(audit_owner)
            )
        )
        admin.execute(
            sql.SQL("ALTER FUNCTION paperpilot_audit.event_fingerprint(uuid) OWNER TO {}").format(
                sql.Identifier(audit_owner)
            )
        )
        admin.execute(
            sql.SQL("REVOKE CREATE ON SCHEMA paperpilot_audit FROM {}").format(sql.Identifier(audit_owner))
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public,paperpilot_audit TO {}").format(
                sql.Identifier(business_role)
            )
        )
        grant_business_fixture(admin, business_role)
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,username,display_name) ON public.users TO {}").format(
                sql.Identifier(business_role)
            )
        )
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_audit.event_fingerprint(uuid) TO {}").format(
                sql.Identifier(business_role)
            )
        )
        assert not admin.execute(
            "SELECT has_table_privilege(%s,'paperpilot_audit.project_events','SELECT')", (business_role,)
        ).fetchone()[0]
        assert not admin.execute(
            "SELECT has_table_privilege(%s,'public.audit_events','SELECT')", (business_role,)
        ).fetchone()[0]

    from dataclasses import replace
    from app.data.audit_boundary import verify_audit_boundary
    from app.data.database import PostgresDatabase

    diagnostic_database = PostgresDatabase(replace(config, user=business_role, password=business_password))
    try:
        assert diagnostic_database.ready()
        with diagnostic_database.transaction() as connection:
            verify_audit_boundary(connection, trusted_owner=audit_owner, scoped_read=True)
    finally:
        diagnostic_database.close()

    def port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    api_port, web_port = port(), port()
    assert api_port != web_port
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(root / "backend"),
            "PAPERPILOT_E2E_PYTHON": sys.executable,
            "PAPERPILOT_ENV": "test",
            "PAPERPILOT_AUTH_MODE": "credentials",
            "PAPERPILOT_AUTH_DATABASE": "isolated",
            "PAPERPILOT_AUTH_LOCK_OWNER": lock_owner,
            "PAPERPILOT_AUTH_DB_ISOLATED_TEST": "1",
            "PAPERPILOT_AUTH_DB_HOST": config.host,
            "PAPERPILOT_AUTH_DB_PORT": str(config.port),
            "PAPERPILOT_AUTH_DB_DBNAME": config.dbname,
            "PAPERPILOT_AUTH_DB_USER": auth_role,
            "PAPERPILOT_AUTH_DB_PASSWORD": auth_password,
            "PAPERPILOT_A_MODE": "persistent",
            "PAPERPILOT_DATA_USER": business_role,
            "PAPERPILOT_DATA_PASSWORD": business_password,
            "PAPERPILOT_C_MODE": "mock",
            "PAPERPILOT_D_MODE": "real",
            "PAPERPILOT_SECURITY_EXECUTION": "candidate",
            "PAPERPILOT_SECURITY_ISOLATED_TEST": "1",
            "PAPERPILOT_PHASE6_BROWSER_ISOLATED": "1",
            "PAPERPILOT_PHASE6_API_PORT": str(api_port),
            "PAPERPILOT_PHASE6_WEB_PORT": str(web_port),
            "PAPERPILOT_ALLOWED_ORIGINS": f"http://127.0.0.1:{web_port}",
            "PAPERPILOT_STORAGE_DIR": str(storage),
            "PAPERPILOT_PHASE67_FIRST_PROJECT_ID": str(project),
            "PAPERPILOT_SECURITY_DB_HOST": config.host,
            "PAPERPILOT_SECURITY_DB_PORT": str(config.port),
            "PAPERPILOT_SECURITY_DB_DBNAME": config.dbname,
            "PAPERPILOT_SECURITY_DB_BINDINGS": json.dumps(
                [
                    {
                        "project_id": str(project),
                        "user": executor.config.user,
                        "password": executor.config.password,
                    },
                    {"project_id": str(other.project_id), "user": stats_role, "password": stats_password},
                ]
            ),
            "PAPERPILOT_SECURITY_DB_VIEW_OWNER": executor.config.view_owner,
            "PAPERPILOT_SECURITY_DB_STATISTICS_SHA256": executor.config.statistics_sha256,
            "PAPERPILOT_SECURITY_DB_TOPICS_SHA256": executor.config.topics_sha256,
            "PAPERPILOT_SCOPED_AUDIT": "isolated",
            "PAPERPILOT_AUDIT_PREFLIGHT": "required",
            "PAPERPILOT_BUSINESS_PRIVILEGE_PREFLIGHT": "required",
            "PAPERPILOT_AUDIT_TRUSTED_OWNER": audit_owner,
            "PAPERPILOT_SCOPED_AUDIT_ISOLATED_TEST": "1",
            "PAPERPILOT_SCOPED_AUDIT_HOST": config.host,
            "PAPERPILOT_SCOPED_AUDIT_PORT": str(config.port),
            "PAPERPILOT_SCOPED_AUDIT_DBNAME": config.dbname,
            "PAPERPILOT_SCOPED_AUDIT_BINDINGS": json.dumps(
                [
                    {
                        "db_user": original.config.user,
                        "db_password": original.config.password,
                        "user_id": str(alice.user_id),
                        "project_id": str(project),
                    },
                    {
                        "db_user": second_role,
                        "db_password": second_password,
                        "user_id": str(bob.user_id),
                        "project_id": str(other.project_id),
                    },
                ]
            ),
            "PAPERPILOT_PHASE67_SECOND_PROJECT_ID": str(other.project_id),
            "PAPERPILOT_SCOPED_AUDIT_OWNER": original.config.owner,
            "PAPERPILOT_SCOPED_AUDIT_BINDING_OWNER": original.config.binding_owner,
            "PAPERPILOT_PHASE67_TEST_PASSWORD": password,
            "PAPERPILOT_PHASE67_FAULT_DIR": str(storage),
        }
    )
    for key in ("PROJECT_ID", "USER", "PASSWORD"):
        environment.pop("PAPERPILOT_SECURITY_DB_" + key, None)
    for key in ("USER", "PASSWORD", "USER_ID", "PROJECT_ID"):
        environment.pop("PAPERPILOT_SCOPED_AUDIT_" + key, None)
    from unittest.mock import patch
    from app.dev_composition import create_app as create_composed

    with patch.dict(os.environ, environment, clear=True):
        composed = create_composed()
        with TestClient(composed) as diagnostic_client:
            assert composed.state.data_binding.ready()
            diagnostic_health = diagnostic_client.get("/api/v1/health")
            assert diagnostic_health.status_code == 200, diagnostic_health.json()
            csrf = diagnostic_client.get("/api/v1/auth/csrf").json()["csrf_token"]
            diagnostic_headers = {"Origin": f"http://127.0.0.1:{web_port}", "X-CSRF-Token": csrf}
            assert (
                diagnostic_client.post(
                    "/api/v1/auth/login",
                    headers=diagnostic_headers,
                    json={"username": "alice", "password": password},
                ).status_code
                == 200
            )
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute(
                    sql.SQL("GRANT DELETE ON public.report_versions TO {}").format(
                        sql.Identifier(business_role)
                    )
                )
            assert diagnostic_client.get("/api/v1/health").status_code == 503
            assert diagnostic_client.get("/api/v1/auth/me").status_code == 503
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute(
                    sql.SQL("REVOKE DELETE ON public.report_versions FROM {}").format(
                        sql.Identifier(business_role)
                    )
                )
            for _ in range(40):
                if diagnostic_client.get("/api/v1/health").status_code == 200:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("Business privilege guard did not recover")
            assert diagnostic_client.get("/api/v1/auth/me").status_code == 200

    # Optional isolated container validation: only the explicit local image and
    # the disposable PostgreSQL container supplied by the test script are used.
    if environment.get("PAPERPILOT_CONTAINER_IMAGE"):
        import httpx

        container_env = environment.copy()
        for key in (
            "PAPERPILOT_DATA_PORT",
            "PAPERPILOT_AUTH_DB_PORT",
            "PAPERPILOT_SECURITY_DB_PORT",
            "PAPERPILOT_SCOPED_AUDIT_PORT",
        ):
            container_env[key] = "5432"
        container_env["PAPERPILOT_STORAGE_DIR"] = "/tmp/paperpilot-isolated/uploads"
        names = [
            "PAPERPILOT_ENV",
            "PAPERPILOT_A_MODE",
            "PAPERPILOT_C_MODE",
            "PAPERPILOT_D_MODE",
            "PAPERPILOT_ALLOWED_ORIGINS",
            "PAPERPILOT_STORAGE_DIR",
            "PAPERPILOT_DATA_HOST",
            "PAPERPILOT_DATA_PORT",
            "PAPERPILOT_DATA_USER",
            "PAPERPILOT_DATA_PASSWORD",
            "PAPERPILOT_DATA_DBNAME",
            "PAPERPILOT_AUTH_MODE",
            "PAPERPILOT_AUTH_DATABASE",
            "PAPERPILOT_AUTH_LOCK_OWNER",
            "PAPERPILOT_AUTH_DB_ISOLATED_TEST",
            "PAPERPILOT_AUTH_DB_HOST",
            "PAPERPILOT_AUTH_DB_PORT",
            "PAPERPILOT_AUTH_DB_DBNAME",
            "PAPERPILOT_AUTH_DB_USER",
            "PAPERPILOT_AUTH_DB_PASSWORD",
            "PAPERPILOT_SECURITY_EXECUTION",
            "PAPERPILOT_SECURITY_ISOLATED_TEST",
            "PAPERPILOT_SECURITY_DB_HOST",
            "PAPERPILOT_SECURITY_DB_PORT",
            "PAPERPILOT_SECURITY_DB_DBNAME",
            "PAPERPILOT_SECURITY_DB_BINDINGS",
            "PAPERPILOT_SECURITY_DB_VIEW_OWNER",
            "PAPERPILOT_SECURITY_DB_STATISTICS_SHA256",
            "PAPERPILOT_SECURITY_DB_TOPICS_SHA256",
            "PAPERPILOT_SCOPED_AUDIT",
            "PAPERPILOT_AUDIT_PREFLIGHT",
            "PAPERPILOT_AUDIT_TRUSTED_OWNER",
            "PAPERPILOT_BUSINESS_PRIVILEGE_PREFLIGHT",
            "PAPERPILOT_SCOPED_AUDIT_ISOLATED_TEST",
            "PAPERPILOT_SCOPED_AUDIT_HOST",
            "PAPERPILOT_SCOPED_AUDIT_PORT",
            "PAPERPILOT_SCOPED_AUDIT_DBNAME",
            "PAPERPILOT_SCOPED_AUDIT_BINDINGS",
            "PAPERPILOT_SCOPED_AUDIT_OWNER",
            "PAPERPILOT_SCOPED_AUDIT_BINDING_OWNER",
        ]
        command = [
            "docker",
            "run",
            "--rm",
            "-d",
            "--pull=never",
            "--network",
            "container:" + environment["PAPERPILOT_ISOLATED_CONTAINER"],
        ]
        for name in names:
            command.extend(("-e", name))
        command.append(environment["PAPERPILOT_CONTAINER_IMAGE"])
        child = None
        base = "http://127.0.0.1:" + environment["PAPERPILOT_CONTAINER_API_PORT"]

        def start_container():
            result = subprocess.run(command, env=container_env, capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise AssertionError("Isolated integration container failed to start")
            return result.stdout.strip()

        def wait_ready(client):
            for _ in range(150):
                try:
                    if client.get("/api/v1/health").status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            raise AssertionError("Isolated integration container did not become ready")

        try:
            child = start_container()
            with httpx.Client(base_url=base, timeout=5) as client:
                wait_ready(client)
                csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
                headers = {"Origin": f"http://127.0.0.1:{web_port}", "X-CSRF-Token": csrf}
                assert (
                    client.post(
                        "/api/v1/auth/login",
                        headers=headers,
                        json={"username": "alice", "password": password},
                    ).status_code
                    == 200
                )
                result = client.post(
                    f"/api/v1/projects/{project}/secure-queries",
                    headers=headers,
                    json={"question": "按年份统计论文数量"},
                )
                assert result.status_code == 200
                body = result.json()
                assert body["execution_status"] == "executed" and body["rows"] == [{"year": 2020, "count": 1}]
                request_id = body["request_id"]
                subprocess.run(["docker", "stop", child], capture_output=True, check=True, timeout=30)
                child = None
                child = start_container()
                wait_ready(client)
                assert client.get("/api/v1/auth/me").status_code == 200
                audit = client.get(f"/api/v1/projects/{project}/audit-events")
                assert audit.status_code == 200 and any(
                    event["request_id"] == request_id for event in audit.json()["items"]
                )
        finally:
            if child:
                subprocess.run(["docker", "stop", child], capture_output=True, timeout=30)

    command = [
        "npm",
        "run",
        "test:e2e",
        "--",
        "--config=playwright.phase6.config.ts",
        "phase6-tests/scoped-audit.spec.ts",
    ]
    if sys.platform == "win32":
        command = ["cmd.exe", "/d", "/c", "npm.cmd", *command[1:]]
    stop = threading.Event()
    fault_applied = threading.Event()

    def inject_fault():
        while not stop.is_set():
            if (storage / "fault-request").exists():
                with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                    admin.execute(
                        "DELETE FROM paperpilot_audit.candidate_readers WHERE login_role=%s",
                        (original.config.user,),
                    )
                (storage / "fault-ready").write_text("ready", encoding="utf-8")
                fault_applied.set()
                return
            time.sleep(0.05)

    injector = threading.Thread(target=inject_fault, daemon=True)
    injector.start()
    try:
        result = subprocess.run(
            command,
            cwd=root / "frontend",
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=150,
        )
    finally:
        stop.set()
        injector.join(timeout=5)
    status = result.returncode
    safe_lines = []
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        if any(marker in line for marker in ("Error:", "Expected:", "Received:", "scoped-audit.spec.ts:")):
            if any(
                value and value in line
                for value in (
                    password,
                    business_password,
                    original.config.password,
                    executor.config.password,
                    config.password,
                    second_password,
                    auth_password,
                    stats_password,
                )
            ):
                continue
            safe_lines.append(line)
    assert status == 0, "Isolated credential browser failed: " + " | ".join(safe_lines[-8:])
    assert fault_applied.is_set(), "Isolated audit fault was not injected"
