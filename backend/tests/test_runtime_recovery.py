"""Bounded automatic recovery, heartbeats and fenced outbox replay on disposable PostgreSQL."""

import asyncio
import os
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.adapters.mock_ai import MockAI
from app.data.deliverer import OutboxDeliverer
from app.errors import AppError
from app.schemas import QuestionRequest
from app.services import ResearchService
from security_contracts.postgres_provider import postgres_workspace

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        os.getenv("PAPERPILOT_SECURITY_CURRENT_SOURCE") != "1", reason="Requires merged disposable PostgreSQL"
    ),
]


@pytest.fixture(scope="module")
def pg():
    with postgres_workspace() as pg:
        yield pg


def job_of(pg):
    pg.data.create_task(
        pg.contexts[0],
        "answer",
        {"question": QuestionRequest(question="fixture"), "paper_ids": [pg.first.paper_id]},
        uuid4().hex,
    )
    return pg.data.claim_job()


def expire(pg, job):
    with pg.data.database.transaction(write=True) as conn:
        conn.execute(
            "UPDATE task_attempts SET claimed_at=now()-interval '2 seconds',lease_expires_at=now()-interval '1 second' WHERE task_id=%s AND attempt_no=%s",
            (job.task.task_id, job.task.attempts),
        )


def test_maximum_automatic_recovery_and_explicit_retry(pg):
    first = job_of(pg)
    current = first
    for _ in range(pg.data.settings.max_automatic_attempts - 1):
        expire(pg, current)
        current = pg.data.claim_job()
        assert current.task.task_id == first.task.task_id
    expire(pg, current)
    assert pg.data.claim_job() is None
    task = pg.data.task(first.context, first.task.task_id)
    assert task.status == "failed" and task.error.code == "TASK_RETRY_LIMIT"
    with pytest.raises(AppError):
        pg.data.renew_job(first)
    pg.data.retry_task(first.context, first.task.task_id)
    manual = pg.data.claim_job()
    assert manual.task.attempts == current.task.attempts + 1
    pg.data.fail_job(manual, "FIXTURE_END", "fixture cleanup")


def test_heartbeat_extends_only_current_live_attempt(pg):
    job = job_of(pg)
    with pg.data.database.transaction(write=True) as conn:
        conn.execute(
            "UPDATE task_attempts SET claimed_at=now()-interval '2 seconds',lease_expires_at=now()+interval '1 second' WHERE task_id=%s",
            (job.task.task_id,),
        )
    pg.data.renew_job(job)
    with pg.data.database.transaction() as conn:
        assert conn.execute(
            "SELECT lease_expires_at>now()+interval '100 seconds' AS renewed FROM task_attempts WHERE task_id=%s AND attempt_no=%s",
            (job.task.task_id, job.task.attempts),
        ).fetchone()["renewed"]
    expire(pg, job)
    with pytest.raises(AppError):
        pg.data.renew_job(job)
    newer = pg.data.claim_job()
    with pytest.raises(AppError):
        pg.data.renew_job(job)
    pg.data.fail_job(newer, "FIXTURE_END", "fixture cleanup")


def test_worker_renews_slow_job_and_cleans_heartbeat(pg):
    job = job_of(pg)
    original = pg.data.settings
    pg.data.settings = replace(original, lease_seconds=1)
    # Renew with the test lease first; otherwise the old 120s lease would mask a missing pulse.
    pg.data.renew_job(job)

    class SlowAI(MockAI):
        async def answer(self, *args):
            await asyncio.sleep(1.5)
            return await super().answer(*args)

    service = ResearchService(SimpleNamespace(data=pg.data, ai=SlowAI(pg.data)))
    try:
        asyncio.run(service._run_leased(job))
    finally:
        pg.data.settings = original
    assert pg.data.task(job.context, job.task.task_id).status == "succeeded"


def test_heartbeat_failure_cancels_work_without_publication(pg):
    job = job_of(pg)
    cancelled = []

    class Data:
        settings = SimpleNamespace(lease_seconds=0.03)

        def renew_job(self, job):
            raise AppError(503, "DATA_UNAVAILABLE", "fixture fault")

    service = ResearchService(SimpleNamespace(data=Data()))

    async def blocked(job):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    service.run_job = blocked
    asyncio.run(service._run_leased(job))
    assert cancelled == [True]
    assert pg.data.task(job.context, job.task.task_id).status == "processing"
    pg.data.fail_job(job, "FIXTURE_END", "fixture cleanup")


def pending(pg, delivery):
    owner = pg.data.user_by_name("alice")
    from app.schemas import ProjectCreate

    project = pg.data.create_project(owner.user_id, ProjectCreate(name="outbox recovery fixture"), uuid4())
    return next(row for row in delivery._due_events() if row["project_id"] == project.project_id)


def test_dead_letter_and_manual_replay_preserve_identity_and_idempotence(pg):
    class Down:
        def append_audit(self, event):
            raise RuntimeError("PRIVATE_PROVIDER_DETAIL")

    delivery = OutboxDeliverer(pg.data.database, Down(), max_attempts=2, base_backoff=0.01)
    row = pending(pg, delivery)
    assert not delivery.deliver_one(row)
    with pg.data.database.transaction(write=True) as conn:
        conn.execute("UPDATE outbox_events SET next_attempt_at=now() WHERE event_id=%s", (row["event_id"],))
    assert not delivery.deliver_one(row)
    assert row["event_id"] not in {r["event_id"] for r in delivery._due_events()}
    assert len(delivery.dead_letters()) >= 1
    assert delivery.requeue_dead_letter(row["event_id"]) and not delivery.requeue_dead_letter(row["event_id"])
    delivery.data = pg.data
    assert delivery.deliver_one(row)
    assert not delivery.deliver_one(row)
    with pg.data.database.transaction() as conn:
        state = conn.execute(
            "SELECT delivery_attempts,payload FROM outbox_events WHERE event_id=%s",
            (row["event_id"],),
        ).fetchone()
        assert state["delivery_attempts"] == 3
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM outbox_requeue_history WHERE event_id=%s", (row["event_id"],)
            ).fetchone()["n"]
            == 1
        )
        assert "PRIVATE" not in str(state)
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM audit_events WHERE event_id=%s", (row["event_id"],)
            ).fetchone()["n"]
            == 1
        )


def test_stale_outbox_claim_cannot_confirm_new_owner(pg):
    delivery = OutboxDeliverer(pg.data.database, pg.data, max_attempts=2)
    row = pending(pg, delivery)
    first = delivery._claim(row["event_id"])
    assert delivery._claim(row["event_id"]) is None
    with pg.data.database.transaction(write=True) as conn:
        conn.execute(
            "UPDATE outbox_events SET next_attempt_at=now()-interval '1 second' WHERE event_id=%s",
            (row["event_id"],),
        )
    second = delivery._claim(row["event_id"])
    assert second != first
    assert not delivery._confirm(row["event_id"], first)
    delivery._record_failure(row["event_id"], first, "DATA_UNAVAILABLE")
    assert delivery._confirm(row["event_id"], second)


def test_crashed_delivery_stops_at_budget_and_remains_replayable(pg):
    delivery = OutboxDeliverer(pg.data.database, pg.data, max_attempts=1)
    row = pending(pg, delivery)
    assert delivery._claim(row["event_id"])
    with pg.data.database.transaction(write=True) as conn:
        conn.execute(
            "UPDATE outbox_events SET next_attempt_at=now()-interval '1 second' WHERE event_id=%s",
            (row["event_id"],),
        )
    assert delivery._claim(row["event_id"]) is None
    assert len(delivery.dead_letters()) >= 1
    with pytest.raises(ValueError, match="reconciled"):
        delivery.requeue_dead_letter(row["event_id"])
    assert delivery.requeue_dead_letter(row["event_id"], unknown_reconciled=True)
