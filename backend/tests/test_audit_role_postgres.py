"""Isolated PostgreSQL evidence for the project/system audit read boundary.

The shared database login still needs trusted application-layer project checks.
"""

import secrets
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from app.adapters.ports import Context
from app.data.adapter import PostgresData
from app.data.audit_boundary import verify_audit_boundary
from app.data.database import ConnectionConfig, PostgresDatabase
from app.data.binding import compose_data_binding
from app.data.settings import DataSettings
from app.schemas import AuditEvent, ProjectCreate
from datetime import UTC, datetime

from test_phase5a_persistent import database as isolated_database  # noqa: F401

pytestmark = pytest.mark.real_database


@pytest.fixture
def audit_role(isolated_database):  # noqa: F811
    config, _ = isolated_database
    role = "audit_app_" + uuid4().hex[:12]
    owner = "audit_owner_" + uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    event_id = uuid4()
    binding = compose_data_binding(config, DataSettings())
    try:
        alice = binding.data.user_by_name("alice")
        project = binding.data.create_project(alice.user_id, ProjectCreate(name="Audit fixture"), uuid4())
    finally:
        binding.close()
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("REVOKE CREATE, TEMPORARY ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(config.dbname)
            )
        )
        admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT "
                "NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(owner))
        )
        admin.execute(sql.SQL("GRANT SELECT ON public.audit_events TO {}").format(sql.Identifier(owner)))
        admin.execute(
            sql.SQL("GRANT USAGE, CREATE ON SCHEMA paperpilot_audit TO {}").format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("ALTER VIEW paperpilot_audit.project_events OWNER TO {}").format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("ALTER FUNCTION paperpilot_audit.event_fingerprint(uuid) OWNER TO {}").format(
                sql.Identifier(owner)
            )
        )
        admin.execute(
            sql.SQL("REVOKE CREATE ON SCHEMA paperpilot_audit FROM {}").format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT "
                "NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
        admin.execute(sql.SQL("GRANT INSERT ON public.audit_events TO {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL("GRANT SELECT ON public.users, public.projects, public.project_members TO {}").format(
                sql.Identifier(role)
            )
        )
        admin.execute(sql.SQL("GRANT USAGE ON SCHEMA paperpilot_audit TO {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL("GRANT SELECT ON paperpilot_audit.project_events TO {}").format(sql.Identifier(role))
        )
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_audit.event_fingerprint(uuid) TO {}").format(
                sql.Identifier(role)
            )
        )
        admin.execute(
            "INSERT INTO public.audit_events "
            "(event_id,timestamp,request_id,action,resource_type,decision,execution_status,"
            "duration_ms,mock,event_fingerprint) "
            "VALUES (%s,now(),%s,'fixture.system','fixture','allowed','completed',0,false,%s)",
            (event_id, uuid4(), "a" * 64),
        )
    params = {**config.conninfo(), "user": role, "password": password}
    return params, event_id, Context(alice.user_id, project.project_id, uuid4()), owner, config


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE public.audit_events SET action='changed' WHERE event_id=%s",
        "DELETE FROM public.audit_events WHERE event_id=%s",
    ],
)
def test_business_role_cannot_modify_existing_audit(audit_role, statement):
    params, event_id, _, _, _ = audit_role
    with psycopg.connect(**params) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement, (event_id,))


def test_role_cannot_read_system_audit_but_can_check_replay_fingerprint(audit_role):
    params, event_id, _, _, _ = audit_role
    with psycopg.connect(**params) as conn:
        assert (
            conn.execute(
                "SELECT project_id, action FROM paperpilot_audit.project_events WHERE event_id=%s",
                (event_id,),
            ).fetchone()
            is None
        )
        assert conn.execute("SELECT paperpilot_audit.event_fingerprint(%s)", (event_id,)).fetchone() == (
            "a" * 64,
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT action FROM public.audit_events WHERE event_id=%s", (event_id,))


def test_business_role_can_append_audit(audit_role):
    params, _, _, _, _ = audit_role
    event_id = uuid4()
    with psycopg.connect(**params) as conn:
        conn.execute(
            "INSERT INTO public.audit_events "
            "(event_id,timestamp,request_id,action,resource_type,decision,execution_status,"
            "duration_ms,mock,event_fingerprint) "
            "VALUES (%s,now(),%s,'fixture.append','fixture','allowed','completed',0,false,%s)",
            (event_id, uuid4(), "b" * 64),
        )
        assert conn.execute("SELECT paperpilot_audit.event_fingerprint(%s)", (event_id,)).fetchone() == (
            "b" * 64,
        )


def test_data_port_audit_works_without_base_table_select(audit_role):
    params, _, ctx, _, _ = audit_role
    database = PostgresDatabase(ConnectionConfig(**params))
    data = PostgresData(database, DataSettings())
    event = AuditEvent(
        event_id=uuid4(),
        timestamp=datetime.now(UTC),
        request_id=ctx.request_id,
        actor_id=ctx.user_id,
        project_id=ctx.project_id,
        action="fixture.project",
        resource_type="fixture",
        decision="allowed",
        execution_status="completed",
        duration_ms=0,
        mock=False,
    )
    try:
        data.append_audit(event)
        data.append_audit(event)  # replay must not create a second row
        result = data.audit(
            ctx, action=None, decision=None, actor_id=None, since=None, until=None, offset=0, limit=10
        )
        assert result.total == 1 and result.items[0].event_id == event.event_id
    finally:
        database.close()


def test_audit_preflight_accepts_restricted_role_and_rejects_wrong_owner(audit_role):
    params, _, _, owner, _ = audit_role
    with psycopg.connect(**params, row_factory=dict_row) as conn:
        verify_audit_boundary(conn, trusted_owner=owner)
        with pytest.raises(PermissionError):
            verify_audit_boundary(conn, trusted_owner="incorrect_owner")


def test_scoped_mode_requires_legacy_view_access_to_be_revoked(audit_role):
    params, _, _, owner, config = audit_role
    with psycopg.connect(**params) as conn:
        with pytest.raises(PermissionError, match="view verification"):
            verify_audit_boundary(conn, trusted_owner=owner, scoped_read=True)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("REVOKE SELECT ON paperpilot_audit.project_events FROM {}").format(
                sql.Identifier(params["user"])
            )
        )
    with psycopg.connect(**params) as conn:
        verify_audit_boundary(conn, trusted_owner=owner, scoped_read=True)


def test_audit_preflight_rejects_base_table_read_grant(audit_role):
    params, _, _, owner, config = audit_role
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT SELECT ON public.audit_events TO {}").format(sql.Identifier(params["user"]))
        )
    with psycopg.connect(**params) as conn:
        with pytest.raises(PermissionError, match="base table"):
            verify_audit_boundary(conn, trusted_owner=owner)


def test_audit_preflight_rejects_owner_access_to_extra_table(audit_role):
    params, _, _, owner, config = audit_role
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(sql.SQL("GRANT SELECT ON public.users TO {}").format(sql.Identifier(owner)))
    with psycopg.connect(**params) as conn:
        with pytest.raises(PermissionError, match="owner has unexpected"):
            verify_audit_boundary(conn, trusted_owner=owner)


@pytest.mark.parametrize("recipient", ["business", "owner"])
def test_audit_preflight_rejects_server_file_function_grants(audit_role, recipient):
    params, _, _, owner, config = audit_role
    recipient_role = params["user"] if recipient == "business" else owner
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION pg_catalog.pg_read_file(text) TO {}").format(
                sql.Identifier(recipient_role)
            )
        )
    with psycopg.connect(**params) as conn:
        with pytest.raises(PermissionError, match="server file function"):
            verify_audit_boundary(conn, trusted_owner=owner)


def test_audit_preflight_rejects_unreviewed_security_definer(audit_role):
    params, _, _, owner, config = audit_role
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "CREATE FUNCTION public.audit_fixture_definer() RETURNS int "
            "LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'"
        )
        admin.execute("REVOKE ALL ON FUNCTION public.audit_fixture_definer() FROM PUBLIC")
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION public.audit_fixture_definer() TO {}").format(
                sql.Identifier(params["user"])
            )
        )
    with psycopg.connect(**params) as conn:
        with pytest.raises(PermissionError, match="security definer function"):
            verify_audit_boundary(conn, trusted_owner=owner)


def test_audit_preflight_rejects_owner_sequence_access(audit_role):
    params, _, _, owner, config = audit_role
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("CREATE SEQUENCE public.audit_fixture_sequence")
        admin.execute(
            sql.SQL("GRANT USAGE ON SEQUENCE public.audit_fixture_sequence TO {}").format(
                sql.Identifier(owner)
            )
        )
    with psycopg.connect(**params) as conn:
        with pytest.raises(PermissionError, match="sequence privileges"):
            verify_audit_boundary(conn, trusted_owner=owner)


def test_audit_preflight_accepts_unrelated_inaccessible_sequence(audit_role):
    params, _, _, owner, config = audit_role
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("CREATE SEQUENCE public.audit_fixture_sequence")
    with psycopg.connect(**params) as conn:
        verify_audit_boundary(conn, trusted_owner=owner)


def test_audit_preflight_rejects_view_exposing_system_events(audit_role):
    params, _, _, owner, config = audit_role
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "CREATE OR REPLACE VIEW paperpilot_audit.project_events WITH (security_barrier=true) AS "
            "SELECT event_id,timestamp,request_id,actor_id,project_id,action,resource_type,decision,"
            "execution_status,policy_id,duration_ms,mock FROM public.audit_events"
        )
    with psycopg.connect(**params) as conn:
        with pytest.raises(PermissionError, match="view verification"):
            verify_audit_boundary(conn, trusted_owner=owner)


def test_audit_preflight_rejects_write_grant_on_project_view(audit_role):
    params, _, _, owner, config = audit_role
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT UPDATE ON paperpilot_audit.project_events TO {}").format(
                sql.Identifier(params["user"])
            )
        )
    with psycopg.connect(**params) as conn:
        with pytest.raises(PermissionError, match="view verification"):
            verify_audit_boundary(conn, trusted_owner=owner)


def test_explicit_audit_preflight_command_uses_restricted_identity(audit_role):
    params, _, _, owner, _ = audit_role
    env = os.environ.copy()
    for name, value in params.items():
        if name != "connect_timeout":
            env["PAPERPILOT_DATA_" + name.upper()] = str(value)
    script = Path(__file__).resolve().parents[2] / "scripts/check_audit_boundary.py"
    good = subprocess.run(
        [sys.executable, str(script), "--trusted-owner", owner], env=env, capture_output=True, text=True
    )
    assert good.returncode == 0 and "verified" in good.stdout
    bad = subprocess.run(
        [sys.executable, str(script), "--trusted-owner", "incorrect_owner"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 1 and "failed" in bad.stderr
    assert params["password"] not in good.stdout + good.stderr + bad.stdout + bad.stderr


def test_required_runtime_preflight_rejects_privilege_drift_before_gateway(audit_role, monkeypatch):
    import asyncio

    from app.adapters.data_binding import DataBinding
    from app.config import Settings
    from app.errors import AppError
    from app.main import create_app

    params, _, context, owner, config = audit_role
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT SELECT ON public.schema_migrations TO {}").format(sql.Identifier(params["user"]))
        )
    restricted_config = ConnectionConfig(**params)
    database = PostgresDatabase(restricted_config)
    data = PostgresData(database, DataSettings())
    try:
        app = create_app(
            Settings(
                environment="test", a_mode="persistent", audit_preflight="required", audit_trusted_owner=owner
            ),
            data_binding=DataBinding(data, database.ready, database.close),
        )
        calls = []

        async def gateway(*args):
            calls.append(True)
            raise AssertionError("gateway must not execute")

        monkeypatch.setattr(app.state.adapters.security, "execute", gateway)
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("GRANT SELECT ON public.audit_events TO {}").format(sql.Identifier(params["user"]))
            )
        with pytest.raises(AppError) as failure:
            asyncio.run(app.state.service.secure_query(context, "按年份统计论文数量"))
        assert failure.value.code == "AUDIT_UNAVAILABLE"
        assert calls == []
    finally:
        database.close()
