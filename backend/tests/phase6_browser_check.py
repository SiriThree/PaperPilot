"""Explicit opt-in browser check: disposable PostgreSQL and restricted role."""

import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from test_restricted_executor_postgres import (  # noqa: F401
    compatible_windows_loop,
    isolated_database,
    restricted,
)

pytestmark = pytest.mark.real_database


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_restricted_browser(environment, *, only_phase63=False, extra_env=None):
    config, storage, _, contexts, executor = environment
    root = Path(__file__).resolve().parents[2]
    api_port, web_port = free_port(), free_port()
    assert api_port != web_port
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
            "PAPERPILOT_PHASE6_BROWSER_ISOLATED": "1",
            "PAPERPILOT_PHASE6_API_PORT": str(api_port),
            "PAPERPILOT_PHASE6_WEB_PORT": str(web_port),
            "PAPERPILOT_ALLOWED_ORIGINS": f"http://127.0.0.1:{web_port}",
            "PAPERPILOT_STORAGE_DIR": str(storage),
            "PAPERPILOT_SECURITY_DB_PROJECT_ID": str(contexts[0].project_id),
            "PAPERPILOT_PHASE6_OTHER_PROJECT_ID": str(contexts[1].project_id),
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
    if extra_env:
        env.update(extra_env)
    command = [
        "npm",
        "run",
        "test:e2e",
        "--",
        "--config=playwright.phase6.config.ts",
        "phase6-tests/security-execution.spec.ts",
    ]
    if only_phase63:
        command.extend(["--grep", "Phase 6.3"])
    if sys.platform == "win32":
        command = ["cmd.exe", "/d", "/c", "npm.cmd", *command[1:]]
    result = subprocess.run(
        command, cwd=root / "frontend", env=env, capture_output=True, text=True, timeout=150
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_restricted_sql_in_browser(restricted):  # noqa: F811
    run_restricted_browser(restricted)


def test_phase63_restricted_read_only_browser(restricted):  # noqa: F811
    run_restricted_browser(restricted, only_phase63=True)


def test_phase63_restricted_browser_with_required_audit_preflight(restricted):  # noqa: F811
    """Real restricted business and SQL logins; administrator only provisions fixture.

    The deliberately broad SELECT grants cover this test, not a production least-privilege
    baseline. The preflight validates audit-specific separation, not project-level RLS.
    """
    import psycopg
    from psycopg import sql
    from uuid import uuid4

    config, _, _, _, _ = restricted
    role = "browser_app_" + uuid4().hex[:12]
    owner = "browser_audit_" + uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
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
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public, paperpilot_audit TO {}").format(sql.Identifier(role))
        )
        for table in admin.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'audit_events'"
        ).fetchall():
            admin.execute(
                sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON public.{} TO {}").format(
                    sql.Identifier(table[0]), sql.Identifier(role)
                )
            )
        admin.execute(sql.SQL("GRANT INSERT ON public.audit_events TO {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL("GRANT SELECT ON paperpilot_audit.project_events TO {}").format(sql.Identifier(role))
        )
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_audit.event_fingerprint(uuid) TO {}").format(
                sql.Identifier(role)
            )
        )
        # The executor preflight must handle unrelated sequence objects without casting
        # them as tables, even when the restricted identities have no sequence grants.
        admin.execute("CREATE SEQUENCE public.phase63_unrelated_sequence")
    run_restricted_browser(
        restricted,
        only_phase63=True,
        extra_env={
            "PAPERPILOT_DATA_USER": role,
            "PAPERPILOT_DATA_PASSWORD": password,
            "PAPERPILOT_AUDIT_PREFLIGHT": "required",
            "PAPERPILOT_AUDIT_TRUSTED_OWNER": owner,
        },
    )


def test_phase65_credentials_browser(isolated_database):  # noqa: F811
    """Real cookie session, PostgreSQL store and browser; SQL gateway disabled."""
    from app.data.authentication import PostgresSessions
    from app.data.database import PostgresDatabase

    config, storage = isolated_database
    password = secrets.token_urlsafe(32)
    database = PostgresDatabase(config)
    try:
        PostgresSessions(database).initialize_password("alice", password)
    finally:
        database.close()
    root = Path(__file__).resolve().parents[2]
    api_port, web_port = free_port(), free_port()
    assert api_port != web_port
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(root / "backend"),
            "PAPERPILOT_ENV": "test",
            "PAPERPILOT_AUTH_MODE": "credentials",
            "PAPERPILOT_A_MODE": "persistent",
            "PAPERPILOT_C_MODE": "mock",
            "PAPERPILOT_D_MODE": "mock",
            "PAPERPILOT_SECURITY_EXECUTION": "disabled",
            "PAPERPILOT_PHASE6_BROWSER_ISOLATED": "1",
            "PAPERPILOT_PHASE6_API_PORT": str(api_port),
            "PAPERPILOT_PHASE6_WEB_PORT": str(web_port),
            "PAPERPILOT_SECURITY_DB_PROJECT_ID": "credential-test-unused",
            "PAPERPILOT_ALLOWED_ORIGINS": f"http://127.0.0.1:{web_port}",
            "PAPERPILOT_STORAGE_DIR": str(storage),
            "PAPERPILOT_PHASE65_TEST_PASSWORD": password,
        }
    )
    command = ["npm", "run", "test:e2e", "--", "--config=playwright.phase6.config.ts", "--grep", "Phase 6.5"]
    if sys.platform == "win32":
        command = ["cmd.exe", "/d", "/c", "npm.cmd", *command[1:]]
    result = subprocess.run(
        command, cwd=root / "frontend", env=env, capture_output=True, text=True, timeout=150
    )
    # Browser failure traces can contain input values; never emit the captured output.
    assert result.returncode == 0, (
        "Phase 6.5 credential browser check failed; output suppressed to protect credentials"
    )
