"""Real, disposable SQLite tests; no application database path is inferred."""

import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

from app.adapters.factory import create_adapters
from app.adapters.ports import Context, DataPort
from app.config import Settings
from app.errors import AppError
from app.persistence.adapter import SQLiteData
from app.persistence.database import SQLiteDatabase
from app.schemas import ProjectCreate
from data_contracts.support import event, import_paper, processing_result, ready_paper, report_input
from legacy_sqlite_app import build_legacy_binding

pytestmark = pytest.mark.legacy_persistence


@pytest.fixture
def storage(tmp_path):
    database = SQLiteDatabase(tmp_path / "isolated.sqlite3")
    database.migrate()
    database.seed_development_users()
    data = SQLiteData(database, lease_seconds=1)
    owner = data.user_by_name("alice")
    project = data.create_project(owner.user_id, ProjectCreate(name="真实 SQLite 测试"), uuid4())
    return database, data, Context(owner.user_id, project.project_id, uuid4())


def test_all_frozen_methods_exist_and_are_callable(storage):
    _, data, _ = storage
    methods = [name for name in vars(DataPort) if not name.startswith("_")]
    assert len(methods) == 26
    assert all(callable(getattr(data, name)) for name in methods)


def test_migration_and_seed_repeat_without_overwriting(storage):
    database, data, ctx = storage
    database.migrate()
    database.seed_development_users()
    assert database.ready()
    assert data.projects(ctx.user_id)[0].project_id == ctx.project_id
    with database.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1


def test_explicit_database_management_commands_use_isolated_path(tmp_path):
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / "cli.sqlite3"
    for command in ("migrate", "migrate", "seed-demo", "seed-demo", "check"):
        result = subprocess.run(
            [sys.executable, str(root / "scripts/manage_database.py"), command, "--database", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == f"SQLite {command}: ok (development only)"
        assert str(path) not in result.stdout + result.stderr
    with SQLiteDatabase(path).transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3


def test_migration_refuses_unknown_database_and_does_not_fallback(tmp_path):
    path = tmp_path / "unrecognized.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE unrelated(value TEXT)")
    with pytest.raises(RuntimeError, match="Unrecognized database"):
        settings = Settings(environment="test", a_mode="persistent")
        create_adapters(settings, data_binding=build_legacy_binding(settings, path))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [
            ("unrelated",)
        ]


@pytest.mark.parametrize("corruption", ["checksum", "version", "invalid_file"])
def test_invalid_migration_state_refuses_startup(tmp_path, corruption):
    path = tmp_path / "invalid.sqlite3"
    database = SQLiteDatabase(path)
    if corruption == "invalid_file":
        path.write_bytes(b"not a database")
    else:
        database.migrate()
        with sqlite3.connect(path) as conn:
            if corruption == "checksum":
                conn.execute("UPDATE schema_migrations SET checksum='changed'")
            else:
                conn.execute("UPDATE schema_migrations SET version=999")
    with pytest.raises(RuntimeError):
        settings = Settings(environment="test", a_mode="persistent")
        create_adapters(settings, data_binding=build_legacy_binding(settings, path))


def test_mock_does_not_create_database_and_persistent_forbids_production(tmp_path):
    path = tmp_path / "must-not-exist.sqlite3"
    create_adapters(Settings(environment="test", database_path=path))
    assert not path.exists()
    with pytest.raises(RuntimeError, match="Production forbids Mock"):
        create_adapters(Settings(environment="production", a_mode="persistent", database_path=path))
    assert not path.exists()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO project_members VALUES('missing','missing','member')",
        "UPDATE project_members SET role='invalid'",
        "DELETE FROM project_members WHERE role='owner'",
        "INSERT INTO users VALUES(NULL,'invalid','invalid')",
        "UPDATE tasks SET status='invalid'",
        "UPDATE tasks SET status='succeeded'",
        "UPDATE tasks SET attempts=-1",
        "UPDATE project_papers SET digest='invalid'",
        "INSERT INTO project_papers SELECT project_id,'missing',digest,storage_key,status,removed FROM project_papers",
    ],
)
def test_database_rejects_invalid_rows_even_without_adapter_validation(storage, statement):
    database, data, ctx = storage
    import_paper(data, ctx)
    with pytest.raises(AppError) as error:
        with database.transaction(write=True) as conn:
            conn.execute(statement)
    assert error.value.status == 409
    with database.transaction() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_digest_and_version_unique_constraints(storage):
    database, data, ctx = storage
    _, _, result = ready_paper(data, ctx)
    data.save_report(ctx, report_input(result), "v1")
    with pytest.raises(AppError):
        with database.transaction(write=True) as conn:
            new_id = str(uuid4())
            conn.execute("INSERT INTO papers VALUES(?, 'duplicate', NULL)", (new_id,))
            conn.execute(
                "INSERT INTO project_papers SELECT project_id,?,digest,storage_key,status,removed FROM project_papers",
                (new_id,),
            )
    with pytest.raises(AppError):
        with database.transaction(write=True) as conn:
            conn.execute(
                "INSERT INTO report_versions SELECT ?,report_id,project_id,number,title,research_question,body,created_at FROM report_versions",
                (str(uuid4()),),
            )
    assert len(data.papers(ctx)) == 1


@pytest.mark.parametrize("table", ["report_versions", "report_sources", "audit_events", "domain_events"])
@pytest.mark.parametrize("mutation", ["UPDATE", "DELETE"])
def test_history_is_append_only_in_database(storage, table, mutation):
    database, data, ctx = storage
    _, _, result = ready_paper(data, ctx)
    data.save_report(ctx, report_input(result), "v1")
    data.append_audit(event(ctx))
    column = {
        "report_versions": "body",
        "report_sources": "excerpt",
        "audit_events": "action",
        "domain_events": "operation",
    }[table]
    sql = f"UPDATE {table} SET {column}='changed'" if mutation == "UPDATE" else f"DELETE FROM {table}"
    with pytest.raises(AppError):
        with database.transaction(write=True) as conn:
            conn.execute(sql)


def test_domain_journal_and_import_rollback_together(storage):
    database, data, ctx = storage
    with database.transaction() as conn:
        before = conn.execute("SELECT COUNT(*) FROM domain_events").fetchone()[0]

    def fail_after_writes(name, conn):
        if name == "import_paper":
            assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM domain_events").fetchone()[0] == before + 1
            conn.execute("INSERT INTO users VALUES(NULL,'failure','failure')")

    broken = SQLiteData(database, before_commit=fail_after_writes)
    with pytest.raises(AppError):
        import_paper(broken, ctx)
    with database.transaction() as conn:
        for table in ("papers", "project_papers", "tasks", "task_papers", "idempotency"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM domain_events").fetchone()[0] == before


def test_real_clock_expiry_and_independent_adapter_fence_old_worker(storage):
    database, data, ctx = storage
    paper, task = import_paper(data, ctx)
    old = data.claim_job()
    reopened = SQLiteData(SQLiteDatabase(database.path), lease_seconds=1)
    assert reopened.claim_job() is None
    time.sleep(1.1)
    new = reopened.claim_job()
    assert new.task.task_id == task.task_id and new.task.attempts == 2
    with pytest.raises(AppError) as error:
        data.complete_job(old, processing_result(paper.paper_id))
    assert error.value.code == "TASK_STATE_CONFLICT"
    with pytest.raises(AppError):
        data.fail_job(old, "STALE", "stale worker")
    reopened.complete_job(new, processing_result(paper.paper_id))
    assert data.task(ctx, task.task_id).status == "succeeded"
    assert len(data.sources(ctx, paper.paper_id)) == 1


def test_topic_statistics_has_no_invented_topic(storage):
    _, data, ctx = storage
    import_paper(data, ctx)
    assert data.controlled_statistics(ctx, "by_topic") == (["topic", "count"], [])


def test_journal_correlates_keyword_calls_and_noop_replays(storage):
    database, data, ctx = storage
    data.add_member(username="bob", ctx=ctx)
    data.add_member(username="bob", ctx=ctx)
    with database.transaction() as conn:
        rows = conn.execute("SELECT * FROM domain_events WHERE operation='add_member'").fetchall()
        assert len(rows) == 1
        assert rows[0]["actor_id"] == str(ctx.user_id)
        assert rows[0]["project_id"] == str(ctx.project_id)
        assert rows[0]["request_id"] == str(ctx.request_id)
