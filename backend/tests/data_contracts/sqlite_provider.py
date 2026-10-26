"""Explicit provider: every test gets a new file under pytest tmp_path."""

import sqlite3
from contextlib import contextmanager

from app.errors import AppError
from app.persistence.adapter import SQLiteData
from app.persistence.database import SQLiteDatabase
from .harness import DataHarness


@contextmanager
def build_harness(*, tmp_path):
    database = SQLiteDatabase(tmp_path / "contract.sqlite3")
    database.migrate()
    database.seed_development_users()
    failure = {"operation": None}

    def before_commit(operation, connection):
        if operation == failure["operation"]:
            # A real SQL NOT NULL violation after the adapter's writes, not a pre-call stub failure.
            try:
                connection.execute("INSERT INTO users(user_id,username,display_name) VALUES(NULL,NULL,NULL)")
            except sqlite3.IntegrityError:
                raise AppError(503, "DATA_UNAVAILABLE", "隔离测试中的已确认回滚故障") from None

    @contextmanager
    def failing_commit(operation):
        failure["operation"] = operation
        try:
            yield
        finally:
            failure["operation"] = None

    def expire_claim(job):
        with database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE tasks SET lease_until='2000-01-01T00:00:00+00:00' WHERE task_id=?",
                (str(job.task.task_id),),
            )

    data = SQLiteData(database, before_commit=before_commit)
    yield DataHarness(
        data,
        data.user_by_name("alice"),
        data.user_by_name("bob"),
        data.user_by_name("eve"),
        "database",
        failing_commit,
        reopen=lambda: SQLiteData(SQLiteDatabase(database.path)),
        expire_claim=expire_claim,
    )
