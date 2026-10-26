"""Database acceptance gates; explicitly skipped for the default Mock provider."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.errors import AppError
from app.schemas import TaskStatus
from .support import (
    audit,
    event,
    evidence_result,
    import_paper,
    processing_result,
    ready_paper,
    report_input,
    research_payload,
)

pytestmark = pytest.mark.real_database


def test_reopen_retains_paper_task_report_and_snapshot(real_harness, data, ctx):
    assert real_harness.reopen is not None, "A must supply an independent reconnect fixture"
    paper, task, result = ready_paper(data, ctx)
    version = data.save_report(ctx, report_input(result), "persistent")
    reopened = real_harness.reopen()
    assert reopened.paper(ctx, paper.paper_id).status == "searchable"
    assert reopened.task(ctx, task.task_id).status == TaskStatus.succeeded
    assert reopened.versions(ctx, version.report_id)[0].sources == result.sources


def test_concurrent_duplicate_upload_has_one_winner(real_harness, data, ctx):
    def upload(index):
        try:
            return import_paper(data, ctx, key=f"concurrent-{index}")
        except AppError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(upload, [1, 2]))
    assert sum(isinstance(item, tuple) for item in outcomes) == 1
    assert outcomes.count("DUPLICATE_PAPER") == 1
    assert len(data.papers(ctx)) == 1


def test_concurrent_versions_do_not_overwrite(real_harness, data, ctx):
    _, _, result = ready_paper(data, ctx)
    first = data.save_report(ctx, report_input(result), "v1")

    def save(index):
        try:
            return data.save_report(
                ctx,
                report_input(result, report_id=first.report_id, base_version_id=first.version_id),
                f"concurrent-version-{index}",
            )
        except AppError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save, [1, 2]))
    assert outcomes.count("VERSION_CONFLICT") == 1
    assert [v.number for v in data.versions(ctx, first.report_id)] == [1, 2]


def test_concurrent_workers_claim_once(real_harness, data, ctx):
    _, task = import_paper(data, ctx)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: data.claim_job(), [1, 2]))
    assert sum(job is not None for job in jobs) == 1
    assert next(job for job in jobs if job).task.task_id == task.task_id


def test_expired_claim_recovery_fences_old_worker(real_harness, data, ctx):
    assert real_harness.expire_claim is not None, "A must supply lease expiry/recovery fixture"
    paper, _ = import_paper(data, ctx)
    old = data.claim_job()
    real_harness.expire_claim(old)
    new = data.claim_job()
    assert new.task.task_id == old.task.task_id and new.task.attempts > old.task.attempts
    with pytest.raises(AppError) as error:
        data.complete_job(old, processing_result(paper.paper_id))
    assert error.value.code == "TASK_STATE_CONFLICT"
    data.complete_job(new, processing_result(paper.paper_id))


def test_reopen_preserves_members_evidence_audit_and_idempotency(real_harness, data, ctx):
    assert real_harness.reopen is not None
    data.add_member(ctx, real_harness.member.username)
    paper, task = import_paper(data, ctx, key="reopen-upload")
    result = processing_result(paper.paper_id)
    data.complete_job(data.claim_job(), result)
    data.create_task(ctx, "evidence", research_payload(paper.paper_id), "reopen-evidence")
    extracted = evidence_result(result)
    data.complete_job(data.claim_job(), extracted)
    saved = data.save_report(ctx, report_input(result), "reopen-report")
    logged = event(ctx)
    data.append_audit(logged)
    reopened = real_harness.reopen()
    assert reopened.role(real_harness.member.user_id, ctx.project_id) == "member"
    assert reopened.user_by_name(real_harness.owner.username).user_id == ctx.user_id
    assert reopened.evidence(ctx, paper.paper_id) == [extracted]
    assert audit(reopened, ctx).items == [logged]
    replay_paper, replay_task = import_paper(reopened, ctx, key="reopen-upload")
    assert replay_paper.paper_id == paper.paper_id and replay_task.task_id == task.task_id
    assert reopened.save_report(ctx, report_input(result), "reopen-report").version_id == saved.version_id
