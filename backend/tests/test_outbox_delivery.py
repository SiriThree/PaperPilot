"""Outbox deliverer behavior on the real isolated PostgreSQL schema.

Covers exactly-once audit effect, confirm-only replay after a lost confirmation,
exponential backoff on permanently failing events (ghost references are immutable
by the outbox trigger, so they keep backing off until a dead-letter policy from
A/D exists), concurrent deliverers, and the standalone builder. Skips cleanly
when the day0 PostgreSQL environment is unavailable.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.data.adapter import PostgresData
from app.data.database import PostgresDatabase
from app.data.deliverer import OutboxDeliverer, build_deliverer
from app.data.provider import disposable_database
from app.data.settings import DataSettings
from app.schemas import ProjectCreate

pytestmark = pytest.mark.real_database


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    manager = disposable_database("paperpilot_outbox_")
    try:
        config = manager.__enter__()
    except RuntimeError as exc:  # no test credentials configured
        pytest.skip(f"outbox delivery tests unavailable: {exc}")
    except Exception as exc:  # noqa: BLE001 - any connectivity failure just skips the module
        pytest.skip(f"PostgreSQL day0 environment unreachable: {type(exc).__name__}")
    database = PostgresDatabase(config)
    database.migrate()
    database.seed_development_users()
    data = PostgresData(database, DataSettings.from_env())
    deliverer = OutboxDeliverer(database, data, base_backoff=0.05, max_backoff=0.2)
    state = SimpleNamespace(config=config, database=database, data=data, deliverer=deliverer)
    try:
        yield state
    finally:
        database.close()
        manager.__exit__(None, None, None)


def outbox_event_of(harness, project_id, action="create_project"):
    with harness.database.transaction() as conn:
        return conn.execute(
            "SELECT event_id FROM outbox_events WHERE project_id=%s AND payload->>'action'=%s"
            " ORDER BY created_at LIMIT 1",
            (project_id, action),
        ).fetchone()["event_id"]


def outbox_full_row(harness, event_id):
    with harness.database.transaction() as conn:
        return conn.execute(
            "SELECT event_id, project_id, actor_id, request_id, payload, created_at"
            " FROM outbox_events WHERE event_id=%s",
            (event_id,),
        ).fetchone()


def outbox_state(harness, event_id):
    with harness.database.transaction() as conn:
        return conn.execute(
            "SELECT delivery_attempts, last_error_code, published_at, next_attempt_at"
            " FROM outbox_events WHERE event_id=%s",
            (event_id,),
        ).fetchone()


def audit_rows(harness, event_id):
    with harness.database.transaction() as conn:
        return conn.execute(
            "SELECT event_id, action FROM audit_events WHERE event_id=%s", (event_id,)
        ).fetchall()


def rewind(harness, event_id):
    with harness.database.transaction(write=True) as conn:
        conn.execute(
            "UPDATE outbox_events SET next_attempt_at='2000-01-01' WHERE event_id=%s", (event_id,)
        )


def test_business_outbox_is_delivered_to_audit_exactly_once(harness):
    owner = harness.data.user_by_name("alice")
    project = harness.data.create_project(owner.user_id, ProjectCreate(name="投递器项目一"), uuid4())
    event_id = outbox_event_of(harness, project.project_id)

    assert harness.deliverer.run_once() >= 1
    rows = audit_rows(harness, event_id)
    assert len(rows) == 1 and rows[0]["action"] == "create_project"
    assert outbox_state(harness, event_id)["published_at"] is not None

    harness.deliverer.run_once()  # no due events remain; nothing may duplicate
    assert len(audit_rows(harness, event_id)) == 1


def test_lost_confirmation_confirms_without_duplicate_audit(harness):
    owner = harness.data.user_by_name("alice")
    project = harness.data.create_project(owner.user_id, ProjectCreate(name="投递器项目二"), uuid4())
    event_id = outbox_event_of(harness, project.project_id)

    # Crash simulation: the deliverer's append committed, the confirmation did not.
    harness.data.append_audit(OutboxDeliverer._event(outbox_full_row(harness, event_id)))
    assert len(audit_rows(harness, event_id)) == 1

    assert harness.deliverer.run_once() >= 1
    assert len(audit_rows(harness, event_id)) == 1  # replay dedupes on event_id
    assert outbox_state(harness, event_id)["published_at"] is not None


def test_permanent_failure_backs_off_without_delivery(harness):
    """A malformed payload can never become a valid audit event, so it backs off forever."""
    owner = harness.data.user_by_name("alice")
    event_id = uuid4()
    with harness.database.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO outbox_events(event_id, project_id, actor_id, request_id, payload)"
            " VALUES(%s,%s,%s,%s,%s::jsonb)",
            (
                event_id,
                None,  # project_id is nullable; the malformed duration is the failure source
                owner.user_id,
                uuid4(),
                '{"action":"contract.outbox","resource_type":"project","resource_id":"x",'
                '"decision":"allowed","execution_status":"succeeded","duration_ms":-1,"mock":false}',
            ),
        )

    assert harness.deliverer.run_once() == 0  # claimed and failed, not delivered
    first = outbox_state(harness, event_id)
    assert first["delivery_attempts"] == 1
    assert first["last_error_code"] == "ADAPTER_CONTRACT_ERROR"
    assert first["published_at"] is None and first["next_attempt_at"] > datetime.now(UTC)

    assert harness.deliverer.run_once() == 0  # scheduled in the future: untouched
    assert outbox_state(harness, event_id)["delivery_attempts"] == 1

    rewind(harness, event_id)
    assert harness.deliverer.run_once() == 0
    second = outbox_state(harness, event_id)
    assert second["delivery_attempts"] == 2
    assert second["next_attempt_at"] > datetime.now(UTC)
    assert audit_rows(harness, event_id) == []


def test_concurrent_deliverers_produce_one_audit_row_per_event(harness):
    owner = harness.data.user_by_name("alice")
    project_ids = [
        harness.data.create_project(owner.user_id, ProjectCreate(name=f"并发投递{index}"), uuid4()).project_id
        for index in range(5)
    ]
    event_ids = [outbox_event_of(harness, pid) for pid in project_ids]

    with ThreadPoolExecutor(max_workers=3) as pool:
        delivered = sum(pool.map(lambda _: harness.deliverer.run_once(), range(3)))

    assert delivered >= len(event_ids)
    for event_id in event_ids:
        assert len(audit_rows(harness, event_id)) == 1
        assert outbox_state(harness, event_id)["published_at"] is not None


def test_standalone_builder_delivers_and_reports_pending(harness):
    deliverer = build_deliverer(harness.config, base_backoff=0.05, max_backoff=0.2)
    try:
        owner = harness.data.user_by_name("bob")
        project = harness.data.create_project(owner.user_id, ProjectCreate(name="独立投递器"), uuid4())
        pending_before = deliverer.pending_count()
        assert pending_before >= 1
        assert deliverer.run_once() >= 1
        assert deliverer.pending_count() < pending_before
        assert len(audit_rows(harness, outbox_event_of(harness, project.project_id))) == 1
    finally:
        deliverer.database.close()
