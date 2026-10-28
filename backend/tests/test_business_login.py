"""Business runtime is a separate non-owner login, never the migration administrator."""

from dataclasses import replace
import os
import secrets
from uuid import uuid4

import psycopg
import pytest

from app.data.adapter import PostgresData
from app.data.database import PostgresDatabase
from app.security.provision import create_login, install, rotate_login, revoke_login
from security_contracts.postgres_provider import postgres_workspace
from test_persistent_index import prepare

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        os.getenv("PAPERPILOT_SECURITY_CURRENT_SOURCE") != "1", reason="Requires merged disposable PostgreSQL"
    ),
]


@pytest.fixture(scope="module")
def pg():
    with postgres_workspace() as pg:
        role, password = "pp_business_" + uuid4().hex, secrets.token_urlsafe(32)
        with psycopg.connect(**pg.config.conninfo(), autocommit=True) as admin:
            install(admin, existing_views=True)
            create_login(admin, role, password, business=True)
        pg.admin_config = pg.config
        pg.config = replace(pg.config, user=role, password=password)
        database = PostgresDatabase(pg.config)
        pg.data = PostgresData(database, pg.data.settings)
        try:
            yield pg
        finally:
            database.close()


def test_runtime_login_can_commit_real_index(pg, tmp_path):
    assert pg.data.database.ready()
    job, result, index, _ = prepare(pg, tmp_path)
    pg.data.complete_indexed_job(job, result, index)
    assert pg.data.task(job.context, job.task.task_id).status == "succeeded"
    assert pg.data.indexed_vectors(job.context, index.model, result.sources)


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM audit_events",
        "UPDATE audit_events SET action='tampered'",
        "TRUNCATE audit_events",
        "DROP TABLE chunks",
        "CREATE TABLE public.forbidden(x int)",
        "CREATE TEMP TABLE forbidden(x int)",
        "SELECT * FROM paperpilot_security.project_readers",
        "SELECT * FROM paperpilot_security.service_logins",
        "UPDATE chunks SET content='tampered'",
        "UPDATE embedding_models SET revision='tampered'",
    ],
)
def test_runtime_cannot_administer_or_mutate_history(pg, statement):
    with psycopg.connect(**pg.config.conninfo(), autocommit=True) as runtime:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute(statement)


def test_runtime_rotation_and_revocation_terminate_sessions(pg):
    old = psycopg.connect(**pg.config.conninfo(), autocommit=True)
    try:
        with psycopg.connect(**pg.admin_config.conninfo(), autocommit=True) as admin:
            new_password = secrets.token_urlsafe(32)
            rotate_login(admin, pg.config.user, new_password)
            with pytest.raises(psycopg.Error):
                old.execute("SELECT 1")
            config = replace(pg.config, password=new_password)
            with psycopg.connect(**config.conninfo(), autocommit=True) as current:
                current.execute("SELECT 1")
                revoke_login(admin, config.user)
                with pytest.raises(psycopg.Error):
                    current.execute("SELECT 1")
            with pytest.raises(psycopg.Error):
                psycopg.connect(**config.conninfo())
    finally:
        old.close()
