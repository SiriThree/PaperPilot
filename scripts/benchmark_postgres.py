"""Synthetic-scale performance baseline for member A's PostgreSQL schema.

Creates a disposable database, seeds a realistic synthetic volume (projects,
papers, chunks, tasks with results, reports, 100k audit events, outbox backlog),
times the DataPort access paths before and after the candidate indexes from
docs/data-model/03-resolved-model.md section 7, and captures EXPLAIN ANALYZE
evidence. Writes docs/performance-baseline.json.

The machine is a developer laptop: the numbers are a relative A/B baseline for
index decisions, never a production capacity claim.
"""

import json
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.data.database import ConnectionConfig, PostgresDatabase  # noqa: E402
from app.data.provider import admin_config  # noqa: E402

REPORT = ROOT / "docs" / "performance-baseline.json"

PROJECTS = 200
PAPERS_PER_PROJECT = 20
CHUNKS_PER_PAPER = 8
PENDING_ANSWER_TASKS = 300
EVIDENCE_SHARE = 4  # every Nth paper also gets an evidence result
REPORT_VERSIONS = 6
AUDIT_EVENTS = 100_000
OUTBOX_UNDELIVERED = 12_000
OUTBOX_PUBLISHED = 2_000
MEMBERS_PER_PROJECT = 8

SIXTY_FOUR = "0123456789abcdef" * 4

CANDIDATE_INDEXES = [
    "CREATE INDEX project_members_user ON project_members (user_id)",
    "CREATE INDEX papers_project_year ON papers (project_id, year)",
    "CREATE INDEX audit_project_action_time ON audit_events (project_id, action, timestamp DESC, event_id DESC)",
]

SCENARIOS = {
    "user_projects": (
        "SELECT p.project_id, p.name, p.description, p.owner_id, m.role"
        " FROM projects p JOIN project_members m ON m.project_id=p.project_id WHERE m.user_id=%s",
        "heavy_user",
    ),
    "role_check": (
        "SELECT role FROM project_members WHERE project_id=%s AND user_id=%s",
        "role_check",
    ),
    "papers_year_filter": (
        "SELECT pp.paper_id, p.title, p.year, pp.status FROM project_papers pp"
        " JOIN papers p ON p.metadata_id=pp.metadata_id"
        " WHERE pp.project_id=%s AND pp.removed_at IS NULL"
        " AND (%s::int IS NULL OR p.year >= %s) AND (%s::int IS NULL OR p.year <= %s)"
        " ORDER BY pp.created_at, pp.paper_id",
        "papers_year",
    ),
    "claim_pick": (
        "SELECT t.task_id FROM tasks t WHERE t.status='pending'"
        " OR (t.status='processing' AND EXISTS (SELECT 1 FROM task_attempts a"
        "     WHERE a.task_id=t.task_id AND a.status='processing' AND a.lease_expires_at <= now()))"
        " ORDER BY t.created_at, t.task_id LIMIT 1 FOR UPDATE SKIP LOCKED",
        None,
    ),
    "sources_read": (
        "SELECT citation_id, paper_id, chunk_id, page_number, excerpt, synthetic FROM result_sources"
        " WHERE project_id=%s AND task_id=(SELECT active_process_task_id FROM project_papers"
        "  WHERE project_id=%s AND paper_id=%s AND removed_at IS NULL) AND scope='root' ORDER BY ordinal",
        "sources_read",
    ),
    "audit_first_page": (
        "SELECT event_id FROM audit_events WHERE project_id=%s AND action=%s"
        " ORDER BY timestamp DESC, event_id DESC LIMIT 20",
        "audit_filter",
    ),
    "audit_deep_page": (
        "SELECT event_id FROM audit_events WHERE project_id=%s AND action=%s"
        " ORDER BY timestamp DESC, event_id DESC LIMIT 20 OFFSET 20000",
        "audit_filter",
    ),
    "reports_latest": (
        "SELECT r.report_id, rv.version_id, rv.number FROM reports r"
        " JOIN report_versions rv ON rv.report_id=r.report_id AND rv.number="
        " (SELECT max(number) FROM report_versions WHERE report_id=r.report_id) WHERE r.project_id=%s",
        "sample_project",
    ),
    "missing_evidence_stats": (
        "SELECT pp.paper_id::text AS paper_id, p.title FROM project_papers pp"
        " JOIN papers p ON p.metadata_id=pp.metadata_id"
        " WHERE pp.project_id=%s AND pp.removed_at IS NULL AND NOT EXISTS ("
        "  SELECT 1 FROM task_results tr WHERE tr.project_id=pp.project_id"
        "  AND tr.paper_id=pp.paper_id AND tr.kind='evidence') ORDER BY pp.created_at, pp.paper_id",
        "sample_project",
    ),
    "outbox_due": (
        "SELECT event_id FROM outbox_events WHERE published_at IS NULL AND next_attempt_at <= now()"
        " ORDER BY next_attempt_at, created_at LIMIT 50 FOR UPDATE SKIP LOCKED",
        None,
    ),
}

EXPLAIN_TARGETS = ["user_projects", "audit_deep_page", "papers_year_filter"]


class Seeder:
    """One consistent synthetic volume; every deferred constraint stays satisfied."""

    def __init__(self, conn):
        self.conn = conn

    def many(self, query, rows):
        with self.conn.cursor() as cursor:
            cursor.executemany(query, rows)

    def copy_rows(self, table, columns, rows):
        with self.conn.cursor() as cursor:
            with cursor.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)

    def seed(self):
        conn = self.conn
        users = [uuid4() for _ in range(40)]
        heavy_user = users[0]
        conn.execute("BEGIN")
        self.many(
            "INSERT INTO users(user_id, username, display_name) VALUES(%s,%s,%s)",
            [(u, f"bench-{index}", f"Bench {index}") for index, u in enumerate(users)],
        )

        project_papers, tasks, attempts, results, task_papers = [], [], [], [], []
        chunk_rows, evidence_fields, source_rows = [], [], []
        report_rows, version_rows, report_source_rows = [], [], []
        member_rows = []
        first_paper = None

        for pindex in range(PROJECTS):
            project = uuid4()
            if pindex == 0:
                self.sample_project = project
            owner = users[pindex % len(users)]
            member_rows.append((project, owner, "owner"))
            for offset in range(1, MEMBERS_PER_PROJECT):
                member_rows.append((project, users[(pindex + offset) % len(users)], "member"))
            conn.execute(
                "INSERT INTO projects(project_id, name, owner_id) VALUES(%s,%s,%s)",
                (project, f"基准项目 {pindex}", owner),
            )

            project_first_paper = project_first_chunk = None
            for paper_index in range(PAPERS_PER_PROJECT):
                metadata, paper, task, chunk_id = uuid4(), uuid4(), uuid4(), uuid4()
                if paper_index == 0:
                    project_first_paper, project_first_chunk = paper, chunk_id
                if pindex == 0 and paper_index == 0:
                    first_paper = paper
                conn.execute(
                    "INSERT INTO papers(metadata_id, project_id, title, year) VALUES(%s,%s,%s,%s)",
                    (metadata, project, f"基准论文 {pindex}-{paper_index}", 2020 + paper_index % 5),
                )
                project_papers.append(
                    (
                        paper,
                        project,
                        metadata,
                        owner,
                        f"{pindex * 1000 + paper_index:064x}",
                        f"bench/{paper}.pdf",
                        task,
                    )
                )
                tasks.append(
                    (
                        task,
                        project,
                        owner,
                        uuid4(),
                        "process",
                        "succeeded",
                        1,
                        json.dumps({"paper_ids": [str(paper)]}),
                        SIXTY_FOUR,
                    )
                )
                attempts.append((task, project, owner))
                results.append((task, project, "process", 1, paper, None))
                task_papers.append((task, paper, project))
                for chunk_index in range(CHUNKS_PER_PAPER):
                    chunk_rows.append(
                        (
                            chunk_id if chunk_index == 0 else uuid4(),
                            project,
                            paper,
                            chunk_index + 1,
                            f"基准片段 {pindex}-{paper_index}-{chunk_index}",
                            chunk_index % 2 == 0,
                        )
                    )
                if paper_index % EVIDENCE_SHARE == 0:
                    evidence_task = uuid4()
                    tasks.append(
                        (
                            evidence_task,
                            project,
                            users[1],
                            uuid4(),
                            "evidence",
                            "succeeded",
                            1,
                            json.dumps({"paper_ids": [str(paper)]}),
                            SIXTY_FOUR,
                        )
                    )
                    attempts.append((evidence_task, project, users[1]))
                    results.append((evidence_task, project, "evidence", 1, paper, uuid4()))
                    task_papers.append((evidence_task, paper, project))
                    for field in ("research_question", "method", "results", "conclusion", "limitations"):
                        evidence_fields.append(
                            (evidence_task, field, project, "基准证据", "pending_verification")
                        )
                        source_rows.append(
                            (
                                evidence_task,
                                field,
                                "1",
                                project,
                                field,
                                1,
                                paper,
                                chunk_id,
                                1,
                                "基准片段",
                                False,
                            )
                        )
                source_rows.append(
                    (task, "root", "1", project, None, 1, paper, chunk_id, 1, "基准片段", False)
                )

            report, first_version = uuid4(), uuid4()
            previous = first_version
            version_rows.append((first_version, project, report, 1, None, "基准报告 1", "基准问题", "正文一"))
            for number in range(2, REPORT_VERSIONS + 1):
                version = uuid4()
                version_rows.append(
                    (version, project, report, number, previous, f"基准报告 {number}", "基准问题", "正文")
                )
                previous = version
            report_rows.append((report, project, users[2], previous))
            for citation in (1, 2):
                report_source_rows.append(
                    (
                        previous,
                        str(citation),
                        project,
                        citation,
                        project_first_paper,
                        project_first_chunk,
                        1,
                        "报告来源",
                        False,
                    )
                )

        self.many("INSERT INTO project_members(project_id, user_id, role) VALUES(%s,%s,%s)", member_rows)
        self.many(
            "INSERT INTO project_papers(paper_id, project_id, metadata_id, imported_by, digest, storage_key,"
            " status, index_state, active_process_task_id) VALUES(%s,%s,%s,%s,%s,%s,'searchable','mock_only',%s)",
            project_papers,
        )
        self.many(
            "INSERT INTO tasks(task_id, project_id, actor_id, request_id, kind, status, attempts, payload,"
            " payload_fingerprint) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
            tasks,
        )
        self.many(
            "INSERT INTO task_attempts(task_id, attempt_no, project_id, actor_id, request_id, status,"
            " lease_expires_at, finished_at) VALUES(%s,1,%s,%s,%s,'succeeded', now() + interval '120 seconds', now())",
            [(task, project, actor, uuid4()) for task, project, actor in attempts],
        )
        self.many(
            "INSERT INTO task_results(task_id, project_id, kind, committed_attempt, paper_id, evidence_id,"
            " payload, result_fingerprint) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
            [
                (task, project, kind, attempt, paper, evidence, '{"paper_ids": []}', SIXTY_FOUR)
                for task, project, kind, attempt, paper, evidence in results
            ],
        )
        self.many("INSERT INTO task_papers(task_id, paper_id, project_id) VALUES(%s,%s,%s)", task_papers)
        self.copy_rows(
            "chunks",
            ["chunk_id", "project_id", "paper_id", "page_number", "excerpt", "synthetic"],
            chunk_rows,
        )
        self.many(
            "INSERT INTO evidence_fields(task_id, field_name, project_id, text, status) VALUES(%s,%s,%s,%s,%s)",
            evidence_fields,
        )
        self.many(
            "INSERT INTO result_sources(task_id, scope, citation_id, project_id, field_name, ordinal,"
            " paper_id, chunk_id, page_number, excerpt, synthetic) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            source_rows,
        )
        self.many(
            "INSERT INTO reports(report_id, project_id, created_by, current_version_id) VALUES(%s,%s,%s,%s)",
            report_rows,
        )
        self.many(
            "INSERT INTO report_versions(version_id, project_id, report_id, number, base_version_id,"
            " title, research_question, body, created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [(v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], users[2]) for v in version_rows],
        )
        self.many(
            "INSERT INTO report_version_sources(version_id, citation_id, project_id, ordinal, paper_id,"
            " chunk_id, page_number, excerpt, synthetic) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            report_source_rows,
        )

        for index in range(PENDING_ANSWER_TASKS):
            conn.execute(
                "INSERT INTO tasks(task_id, project_id, actor_id, request_id, kind, status, attempts,"
                " payload, payload_fingerprint) VALUES(%s,%s,%s,%s,'answer','pending',0,%s::jsonb,%s)",
                (
                    uuid4(),
                    self.sample_project,
                    users[3],
                    uuid4(),
                    json.dumps({"question": {"question": "基准问题", "paper_ids": []}, "paper_ids": []}),
                    SIXTY_FOUR,
                ),
            )
        base_time = datetime.now(UTC)
        self.copy_rows(
            "audit_events",
            [
                "event_id",
                "timestamp",
                "request_id",
                "actor_id",
                "project_id",
                "action",
                "resource_type",
                "decision",
                "execution_status",
                "duration_ms",
                "mock",
                "event_fingerprint",
            ],
            (
                (
                    uuid4(),
                    base_time - timedelta(seconds=index),
                    uuid4(),
                    users[index % len(users)],
                    self.sample_project,
                    f"action.{index % 8}",
                    "project",
                    "allowed",
                    "completed",
                    index % 100,
                    False,
                    SIXTY_FOUR,
                )
                for index in range(AUDIT_EVENTS)
            ),
        )
        payload = json.dumps(
            {
                "action": "bench.event",
                "resource_type": "project",
                "resource_id": "x",
                "decision": "allowed",
                "execution_status": "succeeded",
                "duration_ms": 0,
                "mock": False,
            }
        )
        self.copy_rows(
            "outbox_events",
            [
                "event_id",
                "project_id",
                "actor_id",
                "request_id",
                "payload",
                "next_attempt_at",
                "published_at",
            ],
            (
                (
                    uuid4(),
                    self.sample_project,
                    users[0],
                    uuid4(),
                    payload,
                    base_time - timedelta(seconds=index % 3600),
                    base_time if index < OUTBOX_PUBLISHED else None,
                )
                for index in range(OUTBOX_UNDELIVERED + OUTBOX_PUBLISHED)
            ),
        )
        conn.execute("COMMIT")
        conn.execute("ANALYZE")
        return {
            "sample_project": self.sample_project,
            "heavy_user": heavy_user,
            "first_paper": first_paper,
            "role_check": (self.sample_project, heavy_user),
            "papers_year": (self.sample_project, 2022, 2022, 2022, 2022),
            "sources_read": (self.sample_project, self.sample_project, first_paper),
            "audit_filter": (self.sample_project, "action.3"),
        }


def resolve_params(ids, spec):
    if spec is None:
        return ()
    value = ids[spec] if isinstance(spec, str) else spec
    return value if isinstance(value, tuple) else (value,)


def bench(conn, ids, iterations=30):
    results = {}
    for name, (query, spec) in SCENARIOS.items():
        params = resolve_params(ids, spec)
        timings = []
        for iteration in range(iterations + 3):
            start = time.perf_counter()
            if name in {"claim_pick", "outbox_due"}:
                conn.execute("BEGIN")
                conn.execute(query, params).fetchall()
                conn.rollback()
            else:
                conn.execute(query, params).fetchall()
            if iteration >= 3:  # skip warm-ups
                timings.append((time.perf_counter() - start) * 1000)
        results[name] = {
            "p50_ms": round(statistics.median(timings), 3),
            "p95_ms": round(sorted(timings)[int(len(timings) * 0.95) - 1], 3),
        }
    return results


def explain(conn, ids, name):
    query, spec = SCENARIOS[name]
    data = conn.execute(
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query, resolve_params(ids, spec)
    ).fetchone()[0]
    plan = data[0]["Plan"]

    def describe(node):
        label = node["Node Type"]
        if node.get("Index Name"):
            label += f" on {node['Index Name']}"
        return label

    node = plan
    while "Plans" in node and node["Plans"]:
        node = max(node["Plans"], key=lambda child: child["Actual Rows"])
    return {
        "top_node": describe(plan),
        "dominant_child": describe(node),
        "execution_ms": round(plan["Actual Total Time"], 3),
    }


def main():
    admin = admin_config()
    dbname = "paperpilot_bench_" + uuid4().hex[:12]
    with psycopg.connect(**admin.conninfo(), autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    config = ConnectionConfig(
        host=admin.host, port=admin.port, user=admin.user, password=admin.password, dbname=dbname
    )
    record = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scale": {
            "projects": PROJECTS,
            "papers": PROJECTS * PAPERS_PER_PROJECT,
            "chunks": PROJECTS * PAPERS_PER_PROJECT * CHUNKS_PER_PAPER,
            "pending_answer_tasks": PENDING_ANSWER_TASKS,
            "audit_events": AUDIT_EVENTS,
            "outbox_rows": OUTBOX_UNDELIVERED + OUTBOX_PUBLISHED,
        },
        "candidate_indexes": [statement.split(" ON ", 1)[1] for statement in CANDIDATE_INDEXES],
    }
    database = PostgresDatabase(config)
    try:
        database.migrate()
        with psycopg.connect(**config.conninfo()) as conn:
            started = time.perf_counter()
            ids = Seeder(conn).seed()
            record["seed_seconds"] = round(time.perf_counter() - started, 1)

            record["before"] = bench(conn, ids)
            record["explain_before"] = {name: explain(conn, ids, name) for name in EXPLAIN_TARGETS}

            for statement in CANDIDATE_INDEXES:
                conn.execute(statement)
            conn.execute("ANALYZE")

            record["after"] = bench(conn, ids)
            record["explain_after"] = {name: explain(conn, ids, name) for name in EXPLAIN_TARGETS}
            record["noise_floor_reference"] = {
                "scenario": "role_check (pure PK lookup)",
                "p50_ms": record["after"]["role_check"]["p50_ms"],
                "note": "client+driver round-trip floor; differences below ~0.5ms are jitter",
            }
            record["verdicts"] = {
                name: {
                    "p50_before_ms": record["before"][name]["p50_ms"],
                    "p50_after_ms": record["after"][name]["p50_ms"],
                    "improvement_ratio": round(
                        record["before"][name]["p50_ms"] / max(record["after"][name]["p50_ms"], 0.001), 2
                    ),
                }
                for name in SCENARIOS
            }
        REPORT.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report -> {REPORT}")
        print(f"{'scenario':26s} {'before p50':>10s} {'after p50':>10s} {'ratio':>7s}")
        for name, verdict in record["verdicts"].items():
            print(
                f"{name:26s} {verdict['p50_before_ms']:>9.3f} {verdict['p50_after_ms']:>9.3f}"
                f" {verdict['improvement_ratio']:>6.2f}x"
            )
    finally:
        database.close()
        with psycopg.connect(**admin.conninfo(), autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname))
            )


if __name__ == "__main__":
    main()
