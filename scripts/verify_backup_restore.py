"""Backup/restore acceptance for member A's PostgreSQL data layer.

Runs real business transactions on a disposable database, dumps it with pg_dump
(custom format, executed inside the day0 container), destroys the database,
restores it with pg_restore, then re-verifies every resource class through the
DataPort — including idempotent replay and outbox delivery after recovery.
Writes docs/backup-restore-verification.json.

Usage (PowerShell, repository root, day0 container running):
    .\\.venv\\Scripts\\python.exe scripts/verify_backup_restore.py
"""

import json
import shutil
import subprocess
import sys
from uuid import uuid4
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.adapters.ports import Context, FileReference  # noqa: E402
from app.data.adapter import PostgresData  # noqa: E402
from app.data.database import ConnectionConfig, PostgresDatabase  # noqa: E402
from app.data.deliverer import build_deliverer  # noqa: E402
from app.data.provider import admin_config  # noqa: E402
from app.data.settings import DataSettings  # noqa: E402
from app.schemas import AuditEvent, ProjectCreate, ReportSave, Source  # noqa: E402

REPORT = ROOT / "docs" / "backup-restore-verification.json"
CONTAINER = sys.argv[1] if len(sys.argv) > 1 else "paperpilot-day0-postgres-1"


def docker():
    """docker.exe may be missing from a stripped PATH; fall back to the default install."""
    for candidate in (
        r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
        r"C:\ProgramData\DockerDesktop\version-bin\docker.exe",
        "docker",
    ):
        if shutil.which(candidate) or Path(candidate).exists():
            return candidate
    raise RuntimeError("docker executable not found; start Docker Desktop or adjust PATH")


DUMP_PATH = ROOT / ".runtime" / "backup-acceptance.dump"


def business_snapshot(data, ctx):
    papers = data.papers(ctx)
    paper = papers[0]
    return {
        "paper_id": str(paper.paper_id),
        "paper_status": paper.status,
        "sources": [s.model_dump(mode="json") for s in data.sources(ctx, paper.paper_id)],
        "evidence_count": len(data.evidence(ctx, paper.paper_id)),
        "reports": [r.model_dump(mode="json") for r in data.reports(ctx)],
    }


def main():
    admin = admin_config()
    dbname = "paperpilot_backup_" + uuid4().hex[:12]
    with psycopg.connect(**admin.conninfo(), autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    config = ConnectionConfig(
        host=admin.host, port=admin.port, user=admin.user, password=admin.password, dbname=dbname
    )
    record = {"generated_at": datetime.now(UTC).isoformat(), "container": CONTAINER, "checks": []}
    restore_db = None
    database = PostgresDatabase(config)
    try:
        database.migrate()
        database.seed_development_users()
        data = PostgresData(database, DataSettings.from_env())
        owner = data.user_by_name("alice")
        ctx = Context(owner.user_id, uuid4(), uuid4())
        project = data.create_project(owner.user_id, ProjectCreate(name="备份验收项目"), ctx.request_id)
        ctx = Context(owner.user_id, project.project_id, ctx.request_id)

        file = FileReference(f"{uuid4()}.pdf", uuid4(), project.project_id)
        paper, task = data.import_paper(
            ctx, "备份验收论文", 2024, sha256(b"backup-acceptance").hexdigest(), file, "backup-import"
        )
        job = data.claim_job()
        result_sources = [
            Source(
                citation_id="1",
                paper_id=paper.paper_id,
                chunk_id=uuid4(),
                page_number=3,
                excerpt="备份验收片段",
                synthetic=False,
            )
        ]
        from app.schemas import ProcessResult

        data.complete_job(job, ProcessResult(paper_id=paper.paper_id, sources=result_sources))
        data.save_report(
            ctx,
            ReportSave(
                title="备份报告", research_question="备份问题", body="备份正文 [1]", sources=result_sources
            ),
            "backup-report",
        )
        data.append_audit(
            AuditEvent(
                event_id=uuid4(),
                timestamp=datetime.now(UTC),
                request_id=ctx.request_id,
                actor_id=owner.user_id,
                project_id=project.project_id,
                action="backup.probe",
                resource_type="probe",
                decision="allowed",
                execution_status="completed",
                duration_ms=1,
                mock=False,
            )
        )
        before = business_snapshot(data, ctx)
        tracked = (
            "project_papers",
            "chunks",
            "task_results",
            "report_versions",
            "audit_events",
            "outbox_events",
            "idempotency_records",
        )
        counts_before = {table: count_of(database, table) for table in tracked}
        record["checks"].append(
            {
                "name": "business_state_before_backup",
                "passed": True,
                "detail": {**before, "row_counts": counts_before},
            }
        )

        # ---- dump inside the container, stream to the host ----
        DUMP_PATH.parent.mkdir(exist_ok=True)
        docker_path = docker()
        with DUMP_PATH.open("wb") as dump_file:
            result = subprocess.run(
                [docker_path, "exec", CONTAINER, "pg_dump", "-U", "postgres", "-Fc", dbname],
                stdout=dump_file,
                stderr=subprocess.PIPE,
            )
        if result.returncode != 0:
            raise RuntimeError(
                "pg_dump failed: "
                + result.stderr.decode(errors="replace")[:400]
                + " (is the day0 container name correct?)"
            )
        record["checks"].append(
            {
                "name": "pg_dump_custom_format",
                "passed": DUMP_PATH.stat().st_size > 0,
                "detail": {"bytes": DUMP_PATH.stat().st_size},
            }
        )

        # ---- destroy and restore into a fresh database ----
        with psycopg.connect(**admin.conninfo(), autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(dbname)))
        restore_db = "paperpilot_restore_" + uuid4().hex[:12]
        with psycopg.connect(**admin.conninfo(), autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(restore_db)))
        with DUMP_PATH.open("rb") as dump_file:
            result = subprocess.run(
                [
                    docker_path,
                    "exec",
                    "-i",
                    CONTAINER,
                    "pg_restore",
                    "-U",
                    "postgres",
                    "--no-owner",
                    "--dbname",
                    restore_db,
                ],
                stdin=dump_file,
            )
        if result.returncode != 0:
            raise RuntimeError("pg_restore failed")
        record["checks"].append({"name": "pg_restore_into_empty_database", "passed": True})

        # ---- verify the restored database through the DataPort ----
        restored_config = ConnectionConfig(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            dbname=restore_db,
        )
        restored_database = PostgresDatabase(restored_config)
        try:
            restored_database.ready()
            restored = PostgresData(restored_database, DataSettings.from_env())
            after = business_snapshot(restored, ctx)
            match = after == before
            record["checks"].append(
                {
                    "name": "business_state_identical_after_restore",
                    "passed": match,
                    "detail": None if match else {"before": before, "after": after},
                }
            )

            after_counts = {table: count_of(restored_database, table) for table in tracked}
            record["checks"].append(
                {
                    "name": "row_counts_identical_after_restore",
                    "passed": after_counts == counts_before,
                    "detail": {"before": counts_before, "after": after_counts},
                }
            )

            # Idempotent replay after recovery returns the adopted paper/task.
            replay_paper, replay_task = restored.import_paper(
                ctx,
                "备份验收论文",
                2024,
                sha256(b"backup-acceptance").hexdigest(),
                FileReference(f"{uuid4()}.pdf", uuid4(), project.project_id),
                "backup-import",
            )
            record["checks"].append(
                {
                    "name": "idempotent_replay_after_restore",
                    "passed": str(replay_paper.paper_id) == before["paper_id"]
                    and replay_task.task_id == task.task_id,
                }
            )

            # Outbox rows survived and the deliverer still works on restored data.
            deliverer = build_deliverer(restored_config)
            try:
                pending = deliverer.pending_count()
                delivered = deliverer.run_once()
            finally:
                deliverer.database.close()
            record["checks"].append(
                {
                    "name": "outbox_deliverable_after_restore",
                    "passed": delivered >= 1 and pending >= 1,
                    "detail": {"pending_before": pending, "delivered": delivered},
                }
            )
        finally:
            restored_database.close()
    finally:
        database.close()
        with psycopg.connect(**admin.conninfo(), autocommit=True) as connection:
            for name in (dbname, restore_db):
                if name:
                    connection.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                    )
        DUMP_PATH.unlink(missing_ok=True)

    record["passed"] = all(check["passed"] for check in record["checks"])
    REPORT.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    for check in record["checks"]:
        print(f"[{'PASS' if check['passed'] else 'FAIL'}] {check['name']}")
    print(f"report -> {REPORT} ({'all passed' if record['passed'] else 'FAILURES PRESENT'})")
    if not record["passed"]:
        raise SystemExit(1)


def count_of(database, table):
    with database.transaction() as conn:
        return conn.execute(f"SELECT count(*) AS total FROM {table}").fetchone()["total"]


if __name__ == "__main__":
    main()
