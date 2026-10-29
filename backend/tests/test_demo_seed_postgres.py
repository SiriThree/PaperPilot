"""Repeatable demo seed and overview statistics on a disposable PostgreSQL.

Covers the phase acceptance items owned by member A: the demo seed is
repeatable (a second run changes no rows and reports already-* states), the
seeded content is readable through the public DataPort, and the ``overview``
statistics intent reflects the seeded project. Skips cleanly without the day0
PostgreSQL environment, keeping the default Mock baseline intact.
"""

import pytest
from uuid import uuid5, NAMESPACE_URL

from app.adapters.ports import Context
from app.data.adapter import PostgresData
from app.data.database import PostgresDatabase
from app.data.demo_seed import ALICE_ID, PROJECT_ID, seed_demo_project
from app.data.provider import disposable_database
from app.data.settings import DataSettings

pytestmark = pytest.mark.real_database

COUNTED_TABLES = (
    "projects",
    "project_members",
    "papers",
    "project_papers",
    "chunks",
    "tasks",
    "task_attempts",
    "task_results",
    "reports",
    "report_versions",
    "report_version_sources",
    "idempotency_records",
    "audit_events",
    "outbox_events",
)


def _context(action):
    return Context(ALICE_ID, PROJECT_ID, uuid5(NAMESPACE_URL, f"paperpilot/demo-seed-test/{action}"))


def _counts(database):
    with database.transaction() as connection:
        return {
            table: connection.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
            for table in COUNTED_TABLES
        }


@pytest.fixture(scope="module")
def seeded_database():
    manager = disposable_database("paperpilot_demo_seed_")
    try:
        config = manager.__enter__()
    except RuntimeError as exc:
        pytest.skip(f"demo seed database unavailable: {exc}")
    except Exception as exc:  # noqa: BLE001 - any connectivity failure just skips
        pytest.skip(f"PostgreSQL day0 environment unreachable: {type(exc).__name__}")
    database = PostgresDatabase(config)
    try:
        database.migrate()
        seed_demo_project(database, log=None)
        yield database
    finally:
        database.close()
        manager.__exit__(None, None, None)


def test_seed_second_run_changes_no_rows(seeded_database):
    before = _counts(seeded_database)
    manifest = seed_demo_project(seeded_database, log=None)
    after = _counts(seeded_database)
    assert before == after
    assert manifest["created_project"] is False
    assert set(manifest["papers"].values()) == {
        "already-succeeded",
        "already-failed",
    }
    assert manifest["evidence"] == ["already-succeeded", "already-succeeded"]
    assert [version["number"] for version in manifest["report_versions"]] == [1, 2]


def test_seeded_content_readable_through_port(seeded_database):
    data = PostgresData(seeded_database, DataSettings())
    ctx = _context("read-check")
    papers = data.papers(ctx)
    assert len(papers) == 6
    statuses = sorted(paper.status for paper in papers)
    assert statuses == ["failed"] + ["searchable"] * 5
    searchable = next(paper for paper in papers if paper.status == "searchable")
    assert len(data.sources(ctx, searchable.paper_id)) == 2
    versions = data.versions(ctx, data.reports(ctx)[0].report_id)
    assert [version.number for version in versions] == [1, 2]
    assert len(versions[-1].sources) == 3


def test_overview_statistics_reflect_seed(seeded_database):
    data = PostgresData(seeded_database, DataSettings())
    ctx = _context("overview-check")
    columns, rows = data.controlled_statistics(ctx, "overview")
    assert columns == ["metric", "value"]
    values = {row["metric"]: row["value"] for row in rows}
    assert values == {
        "papers_total": 6,
        "papers_searchable": 5,
        "papers_processing": 0,
        "papers_failed": 1,
        "papers_pending": 0,
        "chunks_total": 10,
        "papers_with_evidence": 2,
        "reports": 1,
        "report_versions": 2,
        "members_total": 2,
    }
    year_columns, year_rows = data.controlled_statistics(ctx, "by_year")
    assert year_columns == ["year", "count"]
    assert {row["year"]: row["count"] for row in year_rows} == {
        2021: 1,
        2022: 1,
        2023: 1,
        2024: 1,
        2025: 2,
    }
