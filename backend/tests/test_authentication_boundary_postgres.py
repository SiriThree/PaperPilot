"""Separate authentication connection: isolated candidate, never production acceptance."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import time
from pathlib import Path
import secrets
import os
import subprocess
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from app.config import Settings
from app.data.authentication import PostgresSessions
from app.data.authentication_boundary import AuthenticationBoundary, auth_config_from_env
from app.data.binding import compose_data_binding
from app.data.database import PostgresDatabase
from app.data.settings import DataSettings
from app.errors import AppError
from app.dev_composition import create_app
from test_phase5a_persistent import database  # noqa: F401

pytestmark = pytest.mark.real_database


def protect_test_secret(path, value):
    path.write_text(value, encoding="utf-8")
    if os.name == "nt":
        script = r"""
          $ErrorActionPreference = 'Stop'
          $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
          $acl = [System.Security.AccessControl.FileSecurity]::new()
          $acl.SetOwner($user)
          $acl.SetAccessRuleProtection($true, $false)
          $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($user, 'FullControl', 'Allow'))
          [System.IO.File]::SetAccessControl($env:PAPERPILOT_TEST_SECRET_PATH, $acl)
        """
        env = {**os.environ, "PAPERPILOT_TEST_SECRET_PATH": str(path)}
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env,
            capture_output=True,
            timeout=15,
        )
        if result.returncode:
            raise RuntimeError("Could not secure isolated test credential file")
    else:
        path.chmod(0o600)


def grant_business_fixture(admin, role):
    from app.data.business_boundary import BUSINESS_TABLE_PRIVILEGES

    for table, privileges in BUSINESS_TABLE_PRIVILEGES.items():
        admin.execute(
            sql.SQL("GRANT {} ON public.{} TO {}").format(
                sql.SQL(",").join(sql.SQL(privilege) for privilege in sorted(privileges)),
                sql.Identifier(table),
                sql.Identifier(role),
            )
        )


def install_auth_lock(admin, auth_role):
    """Provision only the test database; the trusted owner cannot log in."""
    owner = "auth_lock_owner_" + uuid4().hex[:10]
    source = (
        Path(__file__).resolve().parents[1] / "app/security/migrations/004_auth_lock_candidate.sql"
    ).read_text(encoding="utf-8-sig")
    admin.execute(source, prepare=False)
    admin.execute(
        sql.SQL(
            "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
        ).format(sql.Identifier(owner))
    )
    admin.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(owner)))
    admin.execute(
        sql.SQL("GRANT USAGE, CREATE ON SCHEMA paperpilot_auth TO {}").format(sql.Identifier(owner))
    )
    admin.execute(
        sql.SQL(
            "GRANT SELECT (user_id,password_hash,disabled), UPDATE (password_hash) ON public.users TO {}"
        ).format(sql.Identifier(owner))
    )
    admin.execute(
        sql.SQL("ALTER FUNCTION paperpilot_auth.lock_credential(uuid) OWNER TO {}").format(
            sql.Identifier(owner)
        )
    )
    admin.execute(sql.SQL("REVOKE CREATE ON SCHEMA paperpilot_auth FROM {}").format(sql.Identifier(owner)))
    admin.execute(sql.SQL("GRANT USAGE ON SCHEMA paperpilot_auth TO {}").format(sql.Identifier(auth_role)))
    admin.execute(
        sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_auth.lock_credential(uuid) TO {}").format(
            sql.Identifier(auth_role)
        )
    )
    return owner


def test_separate_authentication_identity_and_grant_drift(database, tmp_path, monkeypatch):  # noqa: F811
    config, _ = database
    business_role = "business_" + uuid4().hex[:10]
    auth_role = "authentication_" + uuid4().hex[:10]
    business_password, auth_password, login_password = (secrets.token_urlsafe(32) for _ in range(3))
    administrator = PostgresDatabase(config)
    PostgresSessions(administrator).initialize_password("alice", login_password)
    administrator.close()
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("REVOKE CREATE, TEMPORARY ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(config.dbname)
            )
        )
        admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        for role, password in ((business_role, business_password), (auth_role, auth_password)):
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
                ).format(sql.Identifier(role), sql.Literal(password))
            )
            admin.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,username,display_name) ON public.users TO {}").format(
                sql.Identifier(business_role)
            )
        )
        grant_business_fixture(admin, business_role)
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA paperpilot_audit TO {}").format(sql.Identifier(business_role))
        )
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_audit.event_fingerprint(uuid) TO {}").format(
                sql.Identifier(business_role)
            )
        )
        admin.execute(
            sql.SQL("GRANT INSERT ON public.audit_events TO {}").format(sql.Identifier(business_role))
        )
        admin.execute(
            sql.SQL("GRANT SELECT ON public.schema_migrations TO {}").format(sql.Identifier(business_role))
        )
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,username,password_hash,disabled) ON public.users TO {}").format(
                sql.Identifier(auth_role)
            )
        )
        admin.execute(
            sql.SQL(
                "GRANT SELECT,INSERT,UPDATE ON public.auth_sessions, public.auth_login_limits TO {}"
            ).format(sql.Identifier(auth_role))
        )
        admin.execute(sql.SQL("GRANT INSERT ON public.audit_events TO {}").format(sql.Identifier(auth_role)))
        lock_owner = install_auth_lock(admin, auth_role)
    business_config = replace(config, user=business_role, password=business_password)
    auth_config = replace(config, user=auth_role, password=auth_password)
    binding = compose_data_binding(business_config, DataSettings())
    auth_database = PostgresDatabase(auth_config)
    boundary = AuthenticationBoundary(binding.data.database, auth_database, trusted_lock_owner=lock_owner)
    assert boundary.ready()
    from app.data.business_boundary import verify_business_privileges

    with binding.data.database.transaction() as connection:
        verify_business_privileges(connection)
    # Exercise the business API's actual DataPort privileges, not an admin connection.
    from app.adapters.ports import Context
    from app.schemas import ProjectCreate
    from data_contracts.support import event, ready_paper, report_input

    owner = binding.data.user_by_name("alice")
    member = binding.data.user_by_name("bob")
    project = binding.data.create_project(
        owner.user_id, ProjectCreate(name="Restricted business fixture"), uuid4()
    )
    context = Context(owner.user_id, project.project_id, uuid4())
    binding.data.add_member(context, member.username)
    assert binding.data.role(member.user_id, project.project_id) == "member"
    binding.data.remove_member(context, member.user_id)
    paper, task, result = ready_paper(binding.data, context)
    saved = binding.data.save_report(context, report_input(result), "restricted-business-report")
    assert binding.data.paper(context, paper.paper_id).status == "searchable"
    assert binding.data.task(context, task.task_id).status == "succeeded"
    assert binding.data.versions(context, saved.report_id)[0].sources == result.sources
    logged = event(context)
    binding.data.append_audit(logged)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        assert (
            admin.execute(
                "SELECT count(*) FROM public.audit_events WHERE event_id=%s", (logged.event_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            admin.execute(
                "SELECT count(*) FROM public.outbox_events WHERE project_id=%s", (project.project_id,)
            ).fetchone()[0]
            > 0
        )
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT DELETE ON public.report_versions TO {}").format(sql.Identifier(business_role))
        )
    with binding.data.database.transaction() as connection:
        with pytest.raises(PermissionError):
            verify_business_privileges(connection)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("REVOKE DELETE ON public.report_versions FROM {}").format(sql.Identifier(business_role))
        )
    with pytest.raises(AppError, match="认证数据库"):
        AuthenticationBoundary(binding.data.database, auth_database, trusted_lock_owner="wrong_owner").ready()
    with psycopg.connect(**business_config.conninfo()) as business:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            business.execute("SELECT password_hash FROM public.users")
    with psycopg.connect(**auth_config.conninfo()) as auth:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            auth.execute("SELECT * FROM public.projects")
        auth.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            auth.execute("UPDATE public.users SET password_hash='not-allowed' WHERE username='alice'")
    settings = Settings(
        environment="test",
        a_mode="persistent",
        auth_mode="credentials",
        auth_database="isolated",
        auth_lock_owner=lock_owner,
        storage_dir=tmp_path,
        allowed_origins=("http://trusted.test",),
    )
    binding.close()
    auth_database.close()
    auth_secret_file = tmp_path / "protected-auth-credential"
    protect_test_secret(auth_secret_file, auth_password)
    monkeypatch.delenv("PAPERPILOT_AUTH_DB_PASSWORD", raising=False)
    for key, value in {
        "PAPERPILOT_DATA_USER": business_role,
        "PAPERPILOT_DATA_PASSWORD": business_password,
        "PAPERPILOT_AUTH_DB_ISOLATED_TEST": "1",
        "PAPERPILOT_AUTH_DB_HOST": config.host,
        "PAPERPILOT_AUTH_DB_PORT": str(config.port),
        "PAPERPILOT_AUTH_DB_DBNAME": config.dbname,
        "PAPERPILOT_AUTH_DB_USER": auth_role,
        "PAPERPILOT_AUTH_DB_PASSWORD_FILE": str(auth_secret_file),
    }.items():
        monkeypatch.setenv(key, value)
    app = create_app(settings)
    boundary = app.state.auth_boundary
    with TestClient(app, base_url="http://trusted.test") as client:
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        headers = {"Origin": "http://trusted.test", "X-CSRF-Token": csrf}
        login = client.post(
            "/api/v1/auth/login", headers=headers, json={"username": "alice", "password": login_password}
        )
        assert login.status_code == 200, login.json()
        assert client.get("/api/v1/auth/me").status_code == 200
        assert client.get("/api/v1/health").status_code == 200
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_auth.lock_credential(uuid) TO {}").format(
                    sql.Identifier(business_role)
                )
            )
        with pytest.raises(AppError, match="认证数据库"):
            boundary.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("REVOKE EXECUTE ON FUNCTION paperpilot_auth.lock_credential(uuid) FROM {}").format(
                    sql.Identifier(business_role)
                )
            )
            admin.execute(sql.SQL("GRANT SELECT ON public.projects TO {}").format(sql.Identifier(lock_owner)))
        with pytest.raises(AppError, match="认证数据库"):
            boundary.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("REVOKE SELECT ON public.projects FROM {}").format(sql.Identifier(lock_owner))
            )
            admin.execute(
                "CREATE OR REPLACE FUNCTION paperpilot_auth.lock_credential(requested_user uuid) RETURNS TABLE(password_hash text, disabled boolean) LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$ SELECT NULL::text, true WHERE false $$"
            )
        with pytest.raises(AppError, match="认证数据库"):
            boundary.ready()
        source = (
            Path(__file__).resolve().parents[1] / "app/security/migrations/004_auth_lock_candidate.sql"
        ).read_text(encoding="utf-8-sig")
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(source.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"), prepare=False)
            admin.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_auth.lock_credential(uuid) TO {}").format(
                    sql.Identifier(auth_role)
                )
            )
        assert boundary.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(sql.SQL("GRANT SELECT ON public.projects TO {}").format(sql.Identifier(auth_role)))
        with pytest.raises(AppError, match="认证数据库"):
            boundary.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("REVOKE SELECT ON public.projects FROM {}").format(sql.Identifier(auth_role))
            )
            admin.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_audit.event_fingerprint(uuid) TO {}").format(
                    sql.Identifier(auth_role)
                )
            )
        with pytest.raises(AppError, match="认证数据库"):
            boundary.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("REVOKE EXECUTE ON FUNCTION paperpilot_audit.event_fingerprint(uuid) FROM {}").format(
                    sql.Identifier(auth_role)
                )
            )
        assert boundary.ready()
        # The lock function must serialize a stale login with a committed password reset.
        new_password = secrets.token_urlsafe(32)
        new_hash = app.state.sessions.hasher.hash(new_password)
        with psycopg.connect(**config.conninfo()) as reset, ThreadPoolExecutor(max_workers=1) as pool:
            alice_id = reset.execute("SELECT user_id FROM public.users WHERE username='alice'").fetchone()[0]
            reset.execute("UPDATE public.users SET password_hash=%s WHERE user_id=%s", (new_hash, alice_id))
            reset.execute("UPDATE public.auth_sessions SET revoked_at=now() WHERE user_id=%s", (alice_id,))
            pending = pool.submit(app.state.sessions.create, alice_id, login_password)
            deadline = time.monotonic() + 8
            with psycopg.connect(**config.conninfo(), autocommit=True) as observer:
                while time.monotonic() < deadline:
                    waiting = observer.execute(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE usename=%s AND wait_event_type='Lock' AND query LIKE %s)",
                        (auth_role, "%paperpilot_auth.lock_credential%"),
                    ).fetchone()[0]
                    if waiting:
                        break
                    time.sleep(0.05)
            if not waiting:
                reset.rollback()
                pytest.fail("credential login did not wait on the password reset row lock")
            reset.commit()
            with pytest.raises(AppError) as stale:
                pending.result(timeout=8)
            assert stale.value.status == 401
        assert client.get("/api/v1/auth/me").status_code == 401
        assert (
            client.post(
                "/api/v1/auth/login", headers=headers, json={"username": "alice", "password": new_password}
            ).status_code
            == 200
        )
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("GRANT SELECT (password_hash) ON public.users TO {}").format(
                    sql.Identifier(business_role)
                )
            )
        with pytest.raises(AppError, match="认证数据库"):
            boundary.ready()
        assert client.get("/api/v1/health").status_code == 503
        assert client.get("/api/v1/auth/me").status_code == 503
    # Rotate only this disposable auth role after the old process has drained.
    rotated_auth_password = secrets.token_urlsafe(32)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("REVOKE SELECT (password_hash) ON public.users FROM {}").format(
                sql.Identifier(business_role)
            )
        )
        admin.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(auth_role), sql.Literal(rotated_auth_password)
            )
        )
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(**auth_config.conninfo())
    protect_test_secret(auth_secret_file, rotated_auth_password)
    with TestClient(create_app(settings), base_url="http://trusted.test") as client:
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        headers = {"Origin": "http://trusted.test", "X-CSRF-Token": csrf}
        assert (
            client.post(
                "/api/v1/auth/login", headers=headers, json={"username": "alice", "password": new_password}
            ).status_code
            == 200
        )
        assert client.get("/api/v1/health").status_code == 200


def test_auth_config_refuses_shared_remote_and_missing_credentials():
    from app.data.database import ConnectionConfig

    business = ConnectionConfig("127.0.0.1", 1234, "business", "fixture", "isolated")
    env = {
        "PAPERPILOT_AUTH_DB_ISOLATED_TEST": "1",
        "PAPERPILOT_AUTH_DB_HOST": "127.0.0.1",
        "PAPERPILOT_AUTH_DB_PORT": "1234",
        "PAPERPILOT_AUTH_DB_DBNAME": "isolated",
        "PAPERPILOT_AUTH_DB_USER": "auth",
        "PAPERPILOT_AUTH_DB_PASSWORD": "fixture",
    }
    result = auth_config_from_env(env, business)
    assert result.user == "auth" and "fixture" not in repr(result)
    for update in (
        {"PAPERPILOT_AUTH_DB_USER": "business"},
        {"PAPERPILOT_AUTH_DB_HOST": "remote"},
        {"PAPERPILOT_AUTH_DB_PASSWORD": ""},
    ):
        with pytest.raises(RuntimeError):
            auth_config_from_env({**env, **update}, business)
