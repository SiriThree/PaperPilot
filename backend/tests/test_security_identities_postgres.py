"""Opt-in least-privilege audit and registered login lifecycle on a disposable database."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
import os
import secrets
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from app.adapters.ports import Context
from app.errors import AppError
from app.schemas import AuditEvent
from app.security import SecurityAdapter, build_security
from app.security.audit import compose_audit
from app.security.provision import create_login, install, revoke_login, rotate_login
from security_contracts.postgres_provider import postgres_workspace

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        os.getenv("PAPERPILOT_SECURITY_POSTGRES_TESTS") != "1",
        reason="Identity tests require explicit opt-in, Docker and A's pinned reference",
    ),
]


@pytest.fixture(scope="module")
def pg(record_testsuite_property):
    import psycopg

    with postgres_workspace(record_testsuite_property) as workspace:
        with psycopg.connect(**workspace.config.conninfo(), autocommit=True) as admin:
            install(admin, existing_views=True)
            role, password = "pp_audit_" + uuid4().hex, secrets.token_urlsafe(32)
            create_login(admin, role, password)
        workspace.audit_config = replace(workspace.config, user=role, password=password)
        yield workspace


def event(pg):
    ctx = pg.contexts[0]
    return AuditEvent(
        event_id=uuid4(),
        timestamp=datetime.now(UTC),
        request_id=uuid4(),
        actor_id=ctx.user_id,
        project_id=ctx.project_id,
        action="security.identity.test",
        resource_type="secure_query",
        decision="allowed",
        execution_status="pending",
        duration_ms=0,
        mock=False,
    )


def count_event(pg, event_id):
    import psycopg

    with psycopg.connect(**pg.config.conninfo(), autocommit=True) as observer:
        return observer.execute(
            "SELECT count(*) FROM public.audit_events WHERE event_id=%s", (event_id,)
        ).fetchone()[0]


def test_restricted_writer_reuses_a_persistence_idempotency_and_conflict(pg):
    writer = compose_audit(pg.audit_config)
    logged = event(pg)
    try:
        writer.append(logged)
        writer.append(logged)
        assert count_event(pg, logged.event_id) == 1
        with pytest.raises(AppError) as error:
            writer.append(logged.model_copy(update={"action": "changed"}))
        assert error.value.code == "AUDIT_EVENT_CONFLICT" and count_event(pg, logged.event_id) == 1
    finally:
        writer.close()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE public.audit_events SET decision='changed'",
        "DELETE FROM public.audit_events",
        "TRUNCATE public.audit_events",
        "ALTER TABLE public.audit_events DISABLE TRIGGER ALL",
        "SELECT * FROM public.audit_events",
        "SELECT username FROM public.users",
        "SELECT name FROM public.projects",
        "SELECT * FROM public.papers",
        "SELECT * FROM public.outbox_events",
        "SELECT * FROM paperpilot_security.project_readers",
        "SELECT * FROM paperpilot_security.service_logins",
        "SET ROLE postgres",
        "SET SESSION AUTHORIZATION postgres",
        "CREATE TABLE public.audit_backdoor(id int)",
    ],
)
def test_audit_login_cannot_read_content_or_tamper(pg, statement):
    import psycopg

    with psycopg.connect(**pg.audit_config.conninfo(), autocommit=True) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(statement)


def test_unsafe_audit_login_is_rejected_at_construction(pg):
    with pytest.raises(RuntimeError, match="Restricted audit identity could not be verified"):
        compose_audit(pg.config)


def test_d_critical_events_use_independent_writer_not_business_append(pg, monkeypatch):
    writer = compose_audit(pg.audit_config)
    owner = pg.contexts[0]
    ctx = Context(owner.user_id, owner.project_id, uuid4())

    def unexpected(*args):
        pytest.fail("D must use the independently configured audit identity")

    monkeypatch.setattr(pg.data, "append_audit", unexpected)
    try:
        result = asyncio.run(
            build_security(pg.data, readers=pg.readers, audit_append=writer.append).execute(
                ctx, "按年份统计论文数量"
            )
        )
        assert result.execution_status == "executed"
    finally:
        writer.close()


def test_audit_permission_drift_rolls_back_insert_before_acknowledgment(pg):
    import psycopg
    from psycopg import sql

    writer = compose_audit(pg.audit_config)
    logged = event(pg)
    with psycopg.connect(**pg.config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT SELECT(title) ON public.papers TO {}").format(sql.Identifier(pg.audit_config.user))
        )
        try:
            with pytest.raises(Exception):
                writer.append(logged)
            assert count_event(pg, logged.event_id) == 0
        finally:
            admin.execute(
                sql.SQL("REVOKE SELECT(title) ON public.papers FROM {}").format(
                    sql.Identifier(pg.audit_config.user)
                )
            )
            writer.close()


def test_audit_grant_option_is_not_a_valid_append_only_identity(pg):
    import psycopg
    from psycopg import sql

    with psycopg.connect(**pg.config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT INSERT ON public.audit_events TO {} WITH GRANT OPTION").format(
                sql.Identifier(pg.audit_config.user)
            )
        )
        try:
            with pytest.raises(RuntimeError, match="audit identity"):
                compose_audit(pg.audit_config)
        finally:
            admin.execute(
                sql.SQL("REVOKE GRANT OPTION FOR INSERT ON public.audit_events FROM {}").format(
                    sql.Identifier(pg.audit_config.user)
                )
            )


def test_critical_audit_outage_prevents_execution_with_no_business_fallback(pg):
    import psycopg

    role, password = "pp_audit_" + uuid4().hex, secrets.token_urlsafe(32)
    with psycopg.connect(**pg.config.conninfo(), autocommit=True) as admin:
        create_login(admin, role, password)
        writer = compose_audit(replace(pg.config, user=role, password=password))
        revoke_login(admin, role)
    calls = []

    async def execute(*args):
        calls.append(True)
        return ["count"], [{"count": 1}]

    try:
        with pytest.raises(AppError) as error:
            asyncio.run(
                SecurityAdapter(pg.data, query_executor=execute, audit_append=writer.append).execute(
                    pg.contexts[0], "按年份统计论文数量"
                )
            )
        assert error.value.code == "AUDIT_UNAVAILABLE" and calls == []
    finally:
        writer.close()


def test_reader_creation_rotation_and_revocation_use_real_login_sessions(pg):
    import psycopg

    role = "pp_reader_" + uuid4().hex
    first, second = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    owner = pg.contexts[0]
    with psycopg.connect(**pg.config.conninfo(), autocommit=True) as admin:
        create_login(admin, role, first, project_id=owner.project_id)
        config = replace(pg.config, user=role, password=first)
        with psycopg.connect(**config.conninfo(), autocommit=True) as original:
            assert (
                original.execute("SELECT count(*) FROM paperpilot_security.paper_statistics").fetchone()[0]
                == 2
            )
            rotate_login(admin, role, second)
            with pytest.raises(psycopg.Error):
                original.execute("SELECT 1")
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(**config.conninfo())
        config = replace(config, password=second)
        with psycopg.connect(**config.conninfo(), autocommit=True) as updated:
            assert (
                updated.execute("SELECT count(*) FROM paperpilot_security.paper_statistics").fetchone()[0]
                == 2
            )
            revoke_login(admin, role)
            with pytest.raises(psycopg.Error):
                updated.execute("SELECT 1")
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(**config.conninfo())
        assert (
            admin.execute(
                "SELECT 1 FROM paperpilot_security.project_readers WHERE login_role=%s", (role,)
            ).fetchone()
            is None
        )
        assert admin.execute(
            "SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE rolname=%s", (role,)
        ).fetchone() == (False,)
        with pytest.raises(ValueError, match="Revoked"):
            rotate_login(admin, role, first)
        revoke_login(admin, role)


def test_only_registry_owned_role_oid_can_be_changed(pg):
    import psycopg
    from psycopg import sql

    with psycopg.connect(**pg.config.conninfo(), autocommit=True) as admin:
        unmanaged = "pp_reader_" + uuid4().hex
        admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(unmanaged)))
        with pytest.raises(ValueError, match="registry"):
            revoke_login(admin, unmanaged)
        with pytest.raises(psycopg.errors.DuplicateObject):
            create_login(admin, unmanaged, "temporary-test-password", project_id=pg.contexts[0].project_id)
        assert (
            admin.execute(
                "SELECT 1 FROM paperpilot_security.service_logins WHERE login_role=%s", (unmanaged,)
            ).fetchone()
            is None
        )
        assert admin.execute(
            "SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE rolname=%s", (unmanaged,)
        ).fetchone() == (False,)


def test_failed_project_binding_rolls_back_role_creation(pg):
    import psycopg

    role = "pp_reader_" + uuid4().hex
    with psycopg.connect(**pg.config.conninfo(), autocommit=True) as admin:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            create_login(admin, role, secrets.token_urlsafe(32), project_id=uuid4())
        assert admin.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=%s", (role,)).fetchone() is None


def test_lifecycle_refuses_an_enclosing_transaction(pg):
    import psycopg

    with psycopg.connect(**pg.config.conninfo()) as admin:
        with pytest.raises(ValueError, match="autocommit"):
            create_login(
                admin,
                "pp_reader_" + uuid4().hex,
                "temporary-test-password",
                project_id=pg.contexts[0].project_id,
            )


def test_registry_oid_mismatch_never_changes_a_reused_name(pg):
    import psycopg

    role = "pp_reader_" + uuid4().hex
    with psycopg.connect(**pg.config.conninfo(), autocommit=True) as admin:
        create_login(admin, role, secrets.token_urlsafe(32), project_id=pg.contexts[0].project_id)
        oid = admin.execute("SELECT oid FROM pg_catalog.pg_roles WHERE rolname=%s", (role,)).fetchone()[0]
        other = admin.execute(
            "SELECT oid FROM pg_catalog.pg_roles WHERE rolname=%s", (pg.roles[0],)
        ).fetchone()[0]
        admin.execute(
            "UPDATE paperpilot_security.service_logins SET role_oid=%s WHERE login_role=%s", (other, role)
        )
        with pytest.raises(ValueError, match="registry"):
            revoke_login(admin, role)
        assert admin.execute(
            "SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE oid=%s", (oid,)
        ).fetchone() == (True,)


def test_install_never_overwrites_existing_views_or_registry(pg):
    import psycopg

    with psycopg.connect(**pg.config.conninfo(), autocommit=True) as admin:
        before = admin.execute(
            "SELECT pg_get_viewdef('paperpilot_security.paper_statistics'::regclass)"
        ).fetchone()[0]
        for existing_views in (False, True):
            with pytest.raises(psycopg.Error):
                install(admin, existing_views=existing_views)
        assert (
            admin.execute(
                "SELECT pg_get_viewdef('paperpilot_security.paper_statistics'::regclass)"
            ).fetchone()[0]
            == before
        )


def test_lifecycle_cli_uses_environment_secrets_without_printing_them(pg):
    from psycopg.conninfo import make_conninfo

    root = Path(__file__).resolve().parents[2]
    role = "pp_reader_" + uuid4().hex
    first, second = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    environment = dict(
        os.environ, TEST_ADMIN_DSN=make_conninfo(**pg.config.conninfo()), TEST_MANAGED_PASSWORD=first
    )

    def run(command, extra=()):
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts/manage_security.py"),
                command,
                "--admin-dsn-env",
                "TEST_ADMIN_DSN",
                "--role",
                role,
                *extra,
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, "Lifecycle command was not confirmed"
        assert all(
            secret not in result.stdout + result.stderr for secret in (first, second, pg.config.password)
        )

    run(
        "reader-create",
        ["--project", str(pg.contexts[0].project_id), "--password-env", "TEST_MANAGED_PASSWORD"],
    )
    environment["TEST_MANAGED_PASSWORD"] = second
    run("rotate", ["--password-env", "TEST_MANAGED_PASSWORD"])
    run("revoke")
