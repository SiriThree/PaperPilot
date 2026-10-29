"""Outbox deliverer behavior on the real isolated PostgreSQL schema.

Covers audit event deduplication, replay after a lost confirmation,
exponential backoff, bounded failures, concurrent deliverers, and the standalone builder. Skips cleanly
when the day0 PostgreSQL environment is unavailable.
"""

import hashlib
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4

import pytest

from app.data.adapter import PostgresData
from app.data.database import PostgresDatabase
from app.data.database import MIGRATIONS
from app.data.deliverer import OutboxDeliverer, build_deliverer
from app.data.provider import disposable_database
from app.data.settings import DataSettings
from app.errors import AppError
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
        conn.execute("UPDATE outbox_events SET next_attempt_at='2000-01-01' WHERE event_id=%s", (event_id,))


def test_cli_readiness_failure_closes_resources_without_polling(monkeypatch):
    from app.data import deliverer as module

    calls = []

    class Database:
        def ready(self):
            raise AppError(503, "DATA_UNAVAILABLE", "unavailable")

        def close(self):
            calls.append("closed")

    fake = SimpleNamespace(database=Database(), run_once=lambda: calls.append("polled"))
    monkeypatch.setattr("app.data.database.ConnectionConfig.from_env", lambda: object())
    monkeypatch.setattr(module, "build_deliverer", lambda _config: fake)
    with pytest.raises(AppError, match="unavailable"):
        module.main(["--once"])
    assert calls == ["closed"]


def test_delivery_loop_recovers_after_storage_error(harness, monkeypatch):
    from threading import Event

    stop = Event()
    calls = []

    def temporarily_unavailable():
        calls.append(1)
        if len(calls) == 1:
            raise AppError(503, "DATA_UNAVAILABLE", "private diagnostics")
        stop.set()
        return 1

    monkeypatch.setattr(harness.deliverer, "run_once", temporarily_unavailable)
    harness.deliverer.run_forever(interval=0.001, stop=stop)
    assert len(calls) == 2


def test_version_one_database_upgrades_and_rechecks(harness):
    # The separate disposable database is created only on the explicitly selected test server.
    with disposable_database("paperpilot_outbox_upgrade_") as config:
        database = PostgresDatabase(config)
        try:
            source = (MIGRATIONS / "001_initial.sql").read_text(encoding="utf-8")
            with database.transaction(write=True) as conn:
                conn.execute(
                    "CREATE TABLE schema_migrations(version integer PRIMARY KEY,"
                    " checksum varchar(64) NOT NULL, applied_at timestamptz NOT NULL)"
                )
                conn.execute(source)
                conn.execute(
                    "INSERT INTO schema_migrations VALUES(1,%s,now())",
                    (hashlib.sha256(source.encode()).hexdigest(),),
                )
            database.migrate()
            assert database.ready()
            database.migrate()  # Checksums and already applied versions must remain valid.
            with database.transaction() as conn:
                versions = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
                assert [row["version"] for row in versions] == [1, 2, 3, 4, 5, 6]
        finally:
            database.close()


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


def test_business_and_outbox_commit_or_rollback_together(harness, monkeypatch):
    owner = harness.data.user_by_name("alice")
    successful_request = uuid4()
    harness.data.create_project(owner.user_id, ProjectCreate(name="事务提交"), successful_request)
    with harness.database.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM outbox_events WHERE request_id=%s", (successful_request,)
            ).fetchone()["n"]
            == 1
        )

    failed_request = uuid4()

    def fail_before_commit(operation, _connection):
        if operation == "create_project":
            raise AppError(503, "DATA_UNAVAILABLE", "injected rollback")

    monkeypatch.setattr(harness.data, "_before_commit", fail_before_commit)
    with pytest.raises(AppError):
        harness.data.create_project(owner.user_id, ProjectCreate(name="事务回滚"), failed_request)
    with harness.database.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM outbox_events WHERE request_id=%s", (failed_request,)
            ).fetchone()["n"]
            == 0
        )
        assert (
            conn.execute("SELECT count(*) AS n FROM projects WHERE name=%s", ("事务回滚",)).fetchone()["n"]
            == 0
        )


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
    """A malformed payload receives bounded backoff before its attempt limit."""
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


def test_repeated_failure_dead_letters_without_deleting_or_delivering(harness):
    owner = harness.data.user_by_name("alice")
    event_id = uuid4()
    with harness.database.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO outbox_events(event_id, actor_id, request_id, payload) VALUES(%s,%s,%s,%s::jsonb)",
            (event_id, owner.user_id, uuid4(), '{"action":"bad"}'),
        )
    deliverer = OutboxDeliverer(harness.database, harness.data, max_attempts=2)
    row = outbox_full_row(harness, event_id)
    assert not deliverer.deliver_one(row)
    rewind(harness, event_id)
    assert not deliverer.deliver_one(row)
    dead = next(item for item in deliverer.dead_letters() if item["event_id"] == event_id)
    assert dead["delivery_attempts"] == 2
    assert dead["last_error_code"] == "ADAPTER_CONTRACT_ERROR"
    assert dead["last_attempt_at"] is not None and dead["dead_lettered_at"] is not None
    assert not deliverer.deliver_one(row)
    assert audit_rows(harness, event_id) == []
    assert outbox_full_row(harness, event_id)["event_id"] == event_id


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


def test_concurrent_claim_and_expired_worker_fencing(harness):
    owner = harness.data.user_by_name("alice")
    project = harness.data.create_project(owner.user_id, ProjectCreate(name="租约保护"), uuid4())
    event_id = outbox_event_of(harness, project.project_id)
    deliverer = harness.deliverer
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: deliverer._claim(event_id), range(2)))
    assert sorted(claim for claim in claims if claim is not None) == [1]
    rewind(harness, event_id)  # Simulates lease expiry on this isolated test event.
    assert deliverer._claim(event_id) == 2
    assert not deliverer._confirm(event_id, 1)
    deliverer._record_failure(event_id, 1, "DATA_UNAVAILABLE")
    state = outbox_state(harness, event_id)
    assert state["published_at"] is None and state["last_error_code"] is None
    assert deliverer._confirm(event_id, 2)


def test_unknown_confirmation_replays_same_event_after_lease_expiry(harness, monkeypatch):
    owner = harness.data.user_by_name("alice")
    project = harness.data.create_project(owner.user_id, ProjectCreate(name="确认结果未知"), uuid4())
    event_id = outbox_event_of(harness, project.project_id)
    row = outbox_full_row(harness, event_id)
    deliverer = harness.deliverer
    original = deliverer._confirm

    def confirmation_lost(*args):
        raise RuntimeError("simulated unknown confirmation")

    monkeypatch.setattr(deliverer, "_confirm", confirmation_lost)
    with pytest.raises(RuntimeError, match="simulated unknown confirmation"):
        deliverer.deliver_one(row)
    assert len(audit_rows(harness, event_id)) == 1
    assert outbox_state(harness, event_id)["published_at"] is None
    assert deliverer.deliver_one(row) is False
    rewind(harness, event_id)
    monkeypatch.setattr(deliverer, "_confirm", original)
    assert deliverer.deliver_one(row)
    assert len(audit_rows(harness, event_id)) == 1


@pytest.mark.parametrize("already_delivered", [False, True])
def test_repeated_worker_loss_is_bounded_and_requires_reconciliation(harness, already_delivered):
    owner = harness.data.user_by_name("alice")
    project = harness.data.create_project(owner.user_id, ProjectCreate(name="投递结果未知"), uuid4())
    event_id = outbox_event_of(harness, project.project_id)
    row = outbox_full_row(harness, event_id)
    deliverer = OutboxDeliverer(harness.database, harness.data, max_attempts=2)

    # Each claimant disappears without confirming. In one case the first
    # delivery really committed, in the other it never reached the audit sink.
    assert deliverer._claim(event_id) == 1
    if already_delivered:
        harness.data.append_audit(deliverer._event(row))
    rewind(harness, event_id)
    assert deliverer._claim(event_id) == 2
    rewind(harness, event_id)
    assert deliverer._claim(event_id) is None
    assert not deliverer._confirm(event_id, 2)
    state = next(item for item in deliverer.dead_letters() if item["event_id"] == event_id)
    assert state["delivery_attempts"] == 2
    assert state["last_error_code"] == "DELIVERY_OUTCOME_UNKNOWN"
    assert outbox_state(harness, event_id)["published_at"] is None
    assert len(audit_rows(harness, event_id)) == int(already_delivered)
    assert outbox_full_row(harness, event_id)["event_id"] == event_id


def test_dead_letter_requeue_preserves_identity_history_and_fences_old_attempt(harness):
    owner = harness.data.user_by_name("alice")
    project = harness.data.create_project(owner.user_id, ProjectCreate(name="死信对账"), uuid4())
    event_id = outbox_event_of(harness, project.project_id)
    row = outbox_full_row(harness, event_id)
    deliverer = OutboxDeliverer(harness.database, harness.data, max_attempts=1)
    assert deliverer._claim(event_id) == 1
    harness.data.append_audit(deliverer._event(row))  # Delivery committed; acknowledgement lost.
    rewind(harness, event_id)
    assert deliverer._claim(event_id) is None
    with pytest.raises(ValueError, match="reconciled"):
        deliverer.requeue_dead_letter(event_id)
    assert deliverer.requeue_dead_letter(event_id, unknown_reconciled=True)
    assert not deliverer.requeue_dead_letter(event_id, unknown_reconciled=True)
    assert not deliverer._confirm(event_id, 1)
    assert deliverer.deliver_one(row)
    assert len(audit_rows(harness, event_id)) == 1
    with harness.database.transaction() as conn:
        history = conn.execute(
            "SELECT event_id,prior_attempts,prior_error_code,unknown_reconciled"
            " FROM outbox_requeue_history WHERE event_id=%s",
            (event_id,),
        ).fetchall()
        assert len(history) == 1
        assert history[0]["prior_attempts"] == 1
        assert history[0]["prior_error_code"] == "DELIVERY_OUTCOME_UNKNOWN"
        assert history[0]["unknown_reconciled"] is True


def test_new_delivery_process_recovers_an_expired_claim(harness):
    owner = harness.data.user_by_name("alice")
    project = harness.data.create_project(owner.user_id, ProjectCreate(name="投递器重启"), uuid4())
    event_id = outbox_event_of(harness, project.project_id)
    assert harness.deliverer._claim(event_id) == 1  # First worker disappeared before delivery.
    rewind(harness, event_id)  # Emulate lease expiry without waiting 30 seconds.
    root = Path(__file__).resolve().parents[2]
    config = harness.config
    env = {
        **os.environ,
        "PYTHONPATH": str(root / "backend"),
        "PAPERPILOT_DATA_HOST": config.host,
        "PAPERPILOT_DATA_PORT": str(config.port),
        "PAPERPILOT_DATA_USER": config.user,
        "PAPERPILOT_DATA_PASSWORD": config.password,
        "PAPERPILOT_DATA_DBNAME": config.dbname,
    }
    result = subprocess.run(
        [sys.executable, "-m", "app.data.deliverer", "--once"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0  # Do not expose environment or credentials on failure.
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
