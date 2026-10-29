"""Isolated PostgreSQL harness provider for the shared data contract tests.

Usage:
    pytest backend/tests/data_contracts --data-contract-provider app.data.provider:build_harness

Every harness creates and drops its own disposable database. It never reuses a running
database, never reads B's application Settings, and the commit probe writes a real
failing row before COMMIT instead of raising before the call.
"""

import os
from contextlib import contextmanager
from uuid import uuid4

import psycopg
from psycopg import sql

from app.data.adapter import PostgresData
from app.data.database import ConnectionConfig, PostgresDatabase
from app.data.settings import DataSettings
from app.errors import AppError

DB_PREFIX = "paperpilot_contract_"


def admin_config():
    """Admin connection for disposable test databases only; never a business URL."""
    values = {}
    path = os.path.join(".runtime", "day0.env")
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as handle:
            for line in handle.read().splitlines():
                if line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    values.setdefault(key.strip(), value.strip())
    password = os.getenv("PAPERPILOT_TEST_DATA_PASSWORD") or values.get("PAPERPILOT_DAY0_PASSWORD")
    if not password:
        raise RuntimeError(
            "Provider needs PAPERPILOT_TEST_DATA_PASSWORD or .runtime/day0.env from"
            " start-postgres-day0.ps1; no test database is inferred from app settings"
        )
    return ConnectionConfig(
        host=os.getenv("PAPERPILOT_TEST_DATA_HOST", "127.0.0.1"),
        port=int(os.getenv("PAPERPILOT_TEST_DATA_PORT", values.get("PAPERPILOT_DAY0_PORT", "54326"))),
        user=os.getenv("PAPERPILOT_TEST_DATA_USER", "postgres"),
        password=password,
        dbname="postgres",
    )


@contextmanager
def disposable_database(prefix=DB_PREFIX):
    """Create a throwaway database and drop it on exit; yields its ConnectionConfig."""
    admin = admin_config()
    dbname = prefix + uuid4().hex[:12]
    with psycopg.connect(**admin.conninfo(), autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    config = ConnectionConfig(
        host=admin.host, port=admin.port, user=admin.user, password=admin.password, dbname=dbname
    )
    try:
        yield config
    finally:
        try:
            with psycopg.connect(**admin.conninfo(), autocommit=True) as connection:
                connection.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname))
                )
        except psycopg.Error:
            pass


@contextmanager
def build_harness(*, tmp_path):
    from data_contracts.harness import DataHarness  # single canonical import path

    with disposable_database() as config:
        databases = []
        failure = {"operation": None}

        def make_database():
            database = PostgresDatabase(config, pool_size=4)
            databases.append(database)
            return database

        def before_commit(operation, conn):
            if operation != failure["operation"]:
                return
            try:
                conn.execute("INSERT INTO users(user_id, username, display_name) VALUES (NULL, NULL, NULL)")
            except psycopg.Error:
                raise AppError(503, "DATA_UNAVAILABLE", "隔离测试中的已确认回滚故障") from None
            raise AppError(503, "DATA_UNAVAILABLE", "隔离测试中的已确认回滚故障")

        @contextmanager
        def failing_commit(operation):
            failure["operation"] = operation
            try:
                yield
            finally:
                failure["operation"] = None

        database = make_database()
        database.migrate()
        database.seed_development_users()
        data = PostgresData(database, DataSettings.from_env(), before_commit=before_commit)

        def expire_claim(job):
            with database.transaction(write=True) as conn:
                # Backdate claimed_at too, keeping lease_expires_at > claimed_at (CHECK) intact.
                conn.execute(
                    "UPDATE task_attempts SET claimed_at = '1999-12-31T00:00:00+00:00',"
                    " lease_expires_at = '2000-01-01T00:00:00+00:00'"
                    " WHERE task_id = %s AND status = 'processing'",
                    (job.task.task_id,),
                )

        def reopen():
            return PostgresData(make_database(), DataSettings.from_env())

        yield DataHarness(
            data,
            data.user_by_name("alice"),
            data.user_by_name("bob"),
            data.user_by_name("eve"),
            "database",
            failing_commit,
            reopen=reopen,
            expire_claim=expire_claim,
        )
        for database in databases:
            database.close()
