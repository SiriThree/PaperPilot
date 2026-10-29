"""Rebuild and upgrade both released migration histories on disposable PostgreSQL."""

import hashlib
import os
from uuid import uuid4

import pytest

from app.adapters.ports import Context, FileReference
from app.data.adapter import PostgresData
from app.data.database import MIGRATIONS, PostgresDatabase
from app.data.provider import disposable_database
from app.data.settings import DataSettings
from app.schemas import ProjectCreate

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        os.getenv("PAPERPILOT_PHASE5A_ISOLATED") != "1", reason="Explicit isolated server required"
    ),
]

BASELINES = {
    "initial": ["001_initial.sql"],
    "auth_outbox": [
        "001_initial.sql",
        "002_outbox_dead_letter.sql",
        "003_authentication.sql",
        "004_outbox_requeue.sql",
        "005_auth_rate_limit.sql",
        "006_audit_read_boundary.sql",
    ],
    "index_recovery": ["001_initial.sql", "002_process_indexes.sql", "003_recovery.sql"],
    "index_only": ["001_initial.sql", "002_process_indexes.sql"],
}


def install_baseline(db, names):
    with db.transaction(write=True) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations(version integer PRIMARY KEY, checksum varchar(64) NOT NULL, applied_at timestamptz NOT NULL)"
        )
        for name in names:
            source = (MIGRATIONS / name).read_text(encoding="utf-8")
            conn.execute(source, prepare=False)
            conn.execute(
                "INSERT INTO schema_migrations VALUES(%s,%s,now())",
                (int(name[:3]), hashlib.sha256(source.encode()).hexdigest()),
            )


def assert_reconciled(db):
    with db.transaction() as conn:
        assert [
            r["version"]
            for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        ] == list(range(1, 8))
        assert conn.execute("SELECT to_regclass('public.auth_sessions') IS NOT NULL AS present").fetchone()[
            "present"
        ]
        assert conn.execute("SELECT to_regclass('public.process_indexes') IS NOT NULL AS present").fetchone()[
            "present"
        ]
        columns = {
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='outbox_events'"
            ).fetchall()
        }
        assert {
            "last_attempt_at",
            "dead_lettered_at",
            "retry_attempts",
            "claim_token",
            "delivery_lease_until",
            "retry_base_attempts",
        } <= columns
        triggers = {
            r["tgname"]
            for r in conn.execute(
                "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname IN ('process_indexes_immutable','chunk_embeddings_immutable','embedding_models_immutable')"
            ).fetchall()
        }
        assert triggers == {
            "process_indexes_immutable",
            "chunk_embeddings_immutable",
            "embedding_models_immutable",
        }
    assert db.ready()


def test_fresh_database_rebuild_and_repeat():
    with disposable_database("paperpilot_merge_fresh_") as config:
        db = PostgresDatabase(config)
        try:
            db.migrate()
            assert_reconciled(db)
            db.migrate()
            assert_reconciled(db)
        finally:
            db.close()


@pytest.mark.parametrize("baseline", list(BASELINES))
def test_upgrade_preserves_data_and_migration_checksums(baseline):
    with disposable_database("paperpilot_merge_upgrade_") as config:
        db = PostgresDatabase(config)
        try:
            install_baseline(db, BASELINES[baseline])
            db.seed_development_users()
            data = PostgresData(db, DataSettings())
            alice = data.user_by_name("alice")
            project = data.create_project(alice.user_id, ProjectCreate(name="Upgrade fixture"), uuid4())
            ctx = Context(alice.user_id, project.project_id, uuid4())
            paper_id = uuid4()
            paper, task = data.import_paper(
                ctx,
                "Upgrade paper",
                2024,
                hashlib.sha256(b"upgrade fixture").hexdigest(),
                FileReference(f"{paper_id}.pdf", paper_id, project.project_id),
                "upgrade-import",
            )
            with db.transaction() as conn:
                old = {
                    r["version"]: r["checksum"]
                    for r in conn.execute("SELECT version,checksum FROM schema_migrations").fetchall()
                }
                event_count = conn.execute(
                    "SELECT count(*) AS n FROM outbox_events WHERE project_id=%s", (project.project_id,)
                ).fetchone()["n"]
            db.migrate()
            assert_reconciled(db)
            assert data.projects(alice.user_id)[0].project_id == project.project_id
            assert data.paper(ctx, paper.paper_id).title == "Upgrade paper"
            assert data.task(ctx, task.task_id).status == "pending"
            with db.transaction() as conn:
                current = {
                    r["version"]: r["checksum"]
                    for r in conn.execute("SELECT version,checksum FROM schema_migrations").fetchall()
                }
                assert all(current[version] == checksum for version, checksum in old.items())
                assert (
                    conn.execute(
                        "SELECT count(*) AS n FROM outbox_events WHERE project_id=%s", (project.project_id,)
                    ).fetchone()["n"]
                    == event_count
                )
            db.migrate()
        finally:
            db.close()


def test_unknown_checksum_fails_without_partial_upgrade():
    with disposable_database("paperpilot_merge_bad_") as config:
        db = PostgresDatabase(config)
        try:
            install_baseline(db, BASELINES["initial"])
            with db.transaction(write=True) as conn:
                conn.execute("UPDATE schema_migrations SET checksum=%s WHERE version=1", ("0" * 64,))
            with pytest.raises(RuntimeError, match="checksum mismatch"):
                db.migrate()
            with db.transaction() as conn:
                assert conn.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()["n"] == 1
                assert (
                    conn.execute("SELECT to_regclass('public.auth_sessions') AS name").fetchone()["name"]
                    is None
                )
        finally:
            db.close()
