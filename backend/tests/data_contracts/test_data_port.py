"""Behavioral suite: no Mock imports, no dictionaries/tables/ORM inspection."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from app.adapters.ports import Context, FileReference, Job
from app.errors import AppError
from app.schemas import ProjectCreate, TaskStatus
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


def expect_error(code, action):
    with pytest.raises(AppError) as caught:
        action()
    assert caught.value.code == code


def test_user_and_membership_absence(data, contract_harness, ctx):
    assert data.user_by_id(contract_harness.owner.user_id) == contract_harness.owner
    assert data.user_by_name(contract_harness.owner.username) == contract_harness.owner
    assert data.user_by_name("missing-contract-user") is None
    assert data.user_by_id(uuid4()) is None
    assert data.role(contract_harness.outsider.user_id, ctx.project_id) is None
    assert data.role(ctx.user_id, uuid4()) is None


def test_create_project_and_owner_relationship_atomic(data, contract_harness, ctx):
    project = next(p for p in data.projects(ctx.user_id) if p.project_id == ctx.project_id)
    assert project.owner_id == ctx.user_id and project.role == "owner"
    assert data.role(ctx.user_id, ctx.project_id) == "owner"
    members = data.members(ctx)
    assert len(members) == 1 and members[0].user.user_id == ctx.user_id
    expect_error(
        "RESOURCE_NOT_FOUND",
        lambda: data.create_project(uuid4(), ProjectCreate(name="无效用户项目"), uuid4()),
    )


def test_member_add_repeat_remove_and_roles(data, contract_harness, ctx):
    member = contract_harness.member
    data.add_member(ctx, member.username)
    data.add_member(ctx, member.username)
    assert len(data.members(ctx)) == 2
    member_ctx = replace(ctx, user_id=member.user_id)
    assert data.papers(member_ctx) == []
    expect_error("OWNER_REQUIRED", lambda: data.members(member_ctx))
    expect_error("OWNER_REQUIRED", lambda: data.add_member(member_ctx, contract_harness.outsider.username))
    expect_error("OWNER_IMMUTABLE", lambda: data.remove_member(ctx, ctx.user_id))
    data.remove_member(ctx, member.user_id)
    data.remove_member(ctx, member.user_id)
    assert data.role(member.user_id, ctx.project_id) is None
    expect_error("RESOURCE_NOT_FOUND", lambda: data.papers(member_ctx))
    expect_error("USER_NOT_FOUND", lambda: data.add_member(ctx, "missing-contract-user"))


def test_returned_objects_are_detached(data, contract_harness, ctx):
    user = data.user_by_id(ctx.user_id)
    user.display_name = "mutated"
    assert data.user_by_id(ctx.user_id).display_name == contract_harness.owner.display_name
    projects = data.projects(ctx.user_id)
    actual = next(p for p in projects if p.project_id == ctx.project_id)
    actual.name = "mutated"
    assert next(p for p in data.projects(ctx.user_id) if p.project_id == ctx.project_id).name != "mutated"
    paper, _ = import_paper(data, ctx)
    paper.title = "mutated"
    assert data.paper(ctx, paper.paper_id).title != "mutated"


def test_import_atomic_metadata_task_and_duplicate(data, ctx):
    file = FileReference(f"{uuid4()}.pdf", uuid4(), ctx.project_id)
    paper, task = import_paper(data, ctx, file=file, key="import-key")
    assert paper.project_id == ctx.project_id and paper.status == "pending"
    assert data.task(ctx, task.task_id).status == TaskStatus.pending
    assert data.sources(ctx, paper.paper_id) == []
    replay_file = FileReference(f"{uuid4()}.pdf", uuid4(), ctx.project_id)
    again = import_paper(data, ctx, file=replay_file, key="import-key")
    assert (again[0].paper_id, again[1].task_id) == (paper.paper_id, task.task_id)
    expect_error("DUPLICATE_PAPER", lambda: import_paper(data, ctx, key="other-key"))
    expect_error("IDEMPOTENCY_CONFLICT", lambda: import_paper(data, ctx, text="changed", key="import-key"))
    assert len(data.papers(ctx)) == 1


def test_same_file_in_different_projects_is_independent(data, ctx, other_ctx):
    first, _ = import_paper(data, ctx)
    second, _ = import_paper(data, other_ctx)
    assert first.paper_id != second.paper_id
    expect_error("RESOURCE_NOT_FOUND", lambda: data.paper(other_ctx, first.paper_id))


def test_file_reference_scope_and_identifier_conflict(data, ctx, other_ctx):
    foreign = FileReference("fixture.pdf", uuid4(), other_ctx.project_id)
    expect_error("RESOURCE_NOT_FOUND", lambda: import_paper(data, ctx, file=foreign))
    assert data.papers(ctx) == []
    expect_error(
        "DATA_CONTRACT_ERROR",
        lambda: import_paper(data, ctx, file=FileReference("../file.pdf", uuid4(), ctx.project_id)),
    )
    file = FileReference("fixture.pdf", uuid4(), ctx.project_id)
    import_paper(data, ctx, file=file)
    expect_error("IDENTIFIER_CONFLICT", lambda: import_paper(data, ctx, file=file, text="different"))


def test_paper_filters_and_missing_resources(data, ctx):
    paper, _ = import_paper(data, ctx)
    assert data.papers(ctx, 2024, 2024)[0].paper_id == paper.paper_id
    assert data.papers(ctx, 2025, None) == []
    expect_error("DATA_CONTRACT_ERROR", lambda: data.papers(ctx, 2025, 2020))
    for method in (data.paper, data.sources, data.evidence, data.task, data.versions):
        expect_error("RESOURCE_NOT_FOUND", lambda: method(ctx, uuid4()))


def test_task_payload_canonicalization_and_replay(data, ctx):
    paper, _, _ = ready_paper(data, ctx)
    payload = research_payload(paper.paper_id)
    first = data.create_task(ctx, "answer", payload, "task-key")
    reordered = {"paper_ids": payload["paper_ids"], "question": payload["question"].model_dump()}
    assert data.create_task(ctx, "answer", reordered, "task-key").task_id == first.task_id
    changed = deepcopy(payload)
    changed["question"].question = "different"
    expect_error("IDEMPOTENCY_CONFLICT", lambda: data.create_task(ctx, "answer", changed, "task-key"))
    payload["question"].question = "caller mutation"
    job = data.claim_job()
    assert job.payload["question"].question != "caller mutation"
    job.task.status = TaskStatus.succeeded
    assert data.task(ctx, first.task_id).status == TaskStatus.processing


@pytest.mark.parametrize(
    "kind,payload", [("unknown", {}), ("answer", {}), ("process", {}), ("evidence", {"paper_ids": []})]
)
def test_invalid_task_payload_rejected_without_task(data, ctx, kind, payload):
    expect_error("DATA_CONTRACT_ERROR", lambda: data.create_task(ctx, kind, payload, "bad-task"))
    assert data.claim_job() is None


def test_task_payload_cannot_reference_other_project(data, ctx, other_ctx):
    paper, _, _ = ready_paper(data, ctx)
    expect_error(
        "RESOURCE_NOT_FOUND",
        lambda: data.create_task(other_ctx, "answer", research_payload(paper.paper_id), "foreign-task"),
    )
    assert data.claim_job() is None


def test_retry_fences_old_attempt_and_completion_is_idempotent(data, ctx):
    paper, task = import_paper(data, ctx)
    old = data.claim_job()
    assert old.task.attempts == 1 and data.claim_job() is None
    data.fail_job(old, "AI_UNAVAILABLE", "合成失败")
    data.fail_job(old, "AI_UNAVAILABLE", "合成失败")
    retry_ctx = replace(ctx, request_id=uuid4())
    pending = data.retry_task(retry_ctx, task.task_id)
    assert pending.task_id == task.task_id and pending.created_at == task.created_at
    assert pending.request_id == retry_ctx.request_id and pending.error is None
    assert data.paper(ctx, paper.paper_id).status == "pending"
    expect_error("TASK_NOT_RETRYABLE", lambda: data.retry_task(retry_ctx, task.task_id))
    new = data.claim_job()
    assert new.task.attempts == 2
    result = processing_result(paper.paper_id)
    expect_error("TASK_STATE_CONFLICT", lambda: data.complete_job(old, result))
    expect_error("TASK_STATE_CONFLICT", lambda: data.fail_job(old, "AI_UNAVAILABLE", "旧任务"))
    data.complete_job(new, result)
    data.complete_job(new, result)
    assert data.task(ctx, task.task_id).status == TaskStatus.succeeded
    assert len(data.sources(ctx, paper.paper_id)) == 1
    expect_error("TASK_STATE_CONFLICT", lambda: data.fail_job(new, "AI_UNAVAILABLE", "迟到失败"))
    expect_error("TASK_STATE_CONFLICT", lambda: data.complete_job(new, processing_result(paper.paper_id)))


def test_forged_job_context_does_not_overwrite(data, ctx, other_ctx):
    paper, task = import_paper(data, ctx)
    job = data.claim_job()
    forged = Job(job.task.model_copy(deep=True), other_ctx, deepcopy(job.payload))
    expect_error("RESOURCE_NOT_FOUND", lambda: data.complete_job(forged, processing_result(paper.paper_id)))
    expect_error("RESOURCE_NOT_FOUND", lambda: data.fail_job(forged, "FAIL", "bad context"))
    assert data.task(ctx, task.task_id).status == TaskStatus.processing


def test_complete_rechecks_revoked_membership(data, contract_harness, ctx):
    member = contract_harness.member
    data.add_member(ctx, member.username)
    member_ctx = replace(ctx, user_id=member.user_id)
    paper, task = import_paper(data, member_ctx)
    job = data.claim_job()
    data.remove_member(ctx, member.user_id)
    expect_error("RESOURCE_NOT_FOUND", lambda: data.complete_job(job, processing_result(paper.paper_id)))
    # A trusted worker may close this revoked user's already-claimed attempt as failed, without writing results.
    data.fail_job(job, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
    assert data.task(ctx, task.task_id).status == TaskStatus.failed
    assert data.sources(ctx, paper.paper_id) == []


def test_processing_result_must_match_paper_and_not_duplicate_chunks(data, ctx):
    paper, task = import_paper(data, ctx)
    job = data.claim_job()
    expect_error("ADAPTER_CONTRACT_ERROR", lambda: data.complete_job(job, processing_result(uuid4())))
    result = processing_result(paper.paper_id)
    result.sources.append(result.sources[0].model_copy(deep=True))
    expect_error("ADAPTER_CONTRACT_ERROR", lambda: data.complete_job(job, result))
    assert (
        data.sources(ctx, paper.paper_id) == []
        and data.task(ctx, task.task_id).status == TaskStatus.processing
    )


def test_sources_evidence_and_missing_evidence_statistics(data, ctx):
    paper, _, result = ready_paper(data, ctx)
    columns, rows = data.controlled_statistics(ctx, "missing_evidence")
    assert columns == ["paper_id", "title"] and any(row["paper_id"] == str(paper.paper_id) for row in rows)
    task = data.create_task(ctx, "evidence", research_payload(paper.paper_id), "extract")
    job = data.claim_job()
    evidence = evidence_result(result)
    data.complete_job(job, evidence)
    data.complete_job(job, evidence)
    assert data.task(ctx, task.task_id).status == TaskStatus.succeeded
    assert data.evidence(ctx, paper.paper_id) == [evidence]
    assert data.sources(ctx, paper.paper_id) == result.sources
    assert not any(
        row["paper_id"] == str(paper.paper_id)
        for row in data.controlled_statistics(ctx, "missing_evidence")[1]
    )


def test_report_versions_snapshot_idempotency_and_optimistic_conflict(data, ctx):
    _, _, result = ready_paper(data, ctx)
    payload = report_input(result)
    first = data.save_report(ctx, payload, "save-one")
    assert data.save_report(ctx, payload, "save-one").version_id == first.version_id
    second_input = report_input(result, report_id=first.report_id, base_version_id=first.version_id)
    second_input.body = "版本二"
    second = data.save_report(ctx, second_input, "save-two")
    assert second.number == 2 and second.report_id == first.report_id
    expect_error("VERSION_CONFLICT", lambda: data.save_report(ctx, second_input, "save-stale"))
    assert [v.body for v in data.versions(ctx, first.report_id)] == ["版本一 [1]", "版本二"]
    assert data.versions(ctx, first.report_id)[0].sources == result.sources
    payload.sources[0].excerpt = "caller mutation"
    assert data.versions(ctx, first.report_id)[0].sources[0].excerpt != "caller mutation"
    assert data.reports(ctx)[0].version_id == second.version_id


def test_report_snapshot_revalidated_inside_data_boundary(data, ctx, other_ctx):
    _, _, result = ready_paper(data, ctx)
    expect_error("RESOURCE_NOT_FOUND", lambda: data.save_report(other_ctx, report_input(result), "foreign"))
    forged = report_input(result)
    forged.sources[0].excerpt = "fabricated"
    expect_error("INVALID_CITATION", lambda: data.save_report(ctx, forged, "bad-source"))
    assert data.reports(ctx) == [] and data.reports(other_ctx) == []


@pytest.mark.parametrize("method", ["paper", "sources", "evidence", "task", "versions"])
def test_member_of_both_projects_cannot_substitute_resource_ids(data, ctx, other_ctx, method):
    paper, task, result = ready_paper(data, ctx)
    version = data.save_report(ctx, report_input(result), "report")
    resource = {
        "paper": paper.paper_id,
        "sources": paper.paper_id,
        "evidence": paper.paper_id,
        "task": task.task_id,
        "versions": version.report_id,
    }[method]
    expect_error("RESOURCE_NOT_FOUND", lambda: getattr(data, method)(other_ctx, resource))


def test_non_member_cannot_read_or_write(data, contract_harness, ctx):
    foreign = Context(contract_harness.outsider.user_id, ctx.project_id, uuid4())
    expect_error("RESOURCE_NOT_FOUND", lambda: data.papers(foreign))
    expect_error("RESOURCE_NOT_FOUND", lambda: import_paper(data, foreign))
    expect_error(
        "RESOURCE_NOT_FOUND",
        lambda: data.save_report(foreign, report_input(processing_result(uuid4())), "report"),
    )
    expect_error("RESOURCE_NOT_FOUND", lambda: data.controlled_statistics(foreign, "by_year"))
    expect_error("RESOURCE_NOT_FOUND", lambda: audit(data, foreign))


def test_removal_hides_live_resources_preserves_authorized_snapshot(data, ctx):
    paper, task, result = ready_paper(data, ctx)
    report = data.save_report(ctx, report_input(result), "saved")
    data.remove_paper(ctx, paper.paper_id)
    assert data.papers(ctx) == []
    expect_error("RESOURCE_NOT_FOUND", lambda: data.paper(ctx, paper.paper_id))
    expect_error("RESOURCE_NOT_FOUND", lambda: data.task(ctx, task.task_id))
    assert data.versions(ctx, report.report_id)[0].sources == result.sources
    assert data.save_report(ctx, report_input(result), "saved").version_id == report.version_id
    expect_error("DUPLICATE_PAPER", lambda: import_paper(data, ctx))


def test_removed_paper_import_replay_is_not_live_read(data, ctx):
    paper, _ = import_paper(data, ctx, key="removed-import")
    data.remove_paper(ctx, paper.paper_id)
    expect_error("RESOURCE_NOT_FOUND", lambda: import_paper(data, ctx, key="removed-import"))


def test_audit_unique_event_pagination_filters_and_scope(data, ctx, other_ctx, contract_harness):
    first, second = event(ctx), event(ctx, decision="rejected")
    data.append_audit(first)
    data.append_audit(first)
    data.append_audit(second)
    data.append_audit(event(other_ctx))
    expect_error(
        "AUDIT_EVENT_CONFLICT", lambda: data.append_audit(first.model_copy(update={"action": "changed"}))
    )
    page = audit(data, ctx, limit=1)
    assert page.total == 2 and len(page.items) == 1
    assert audit(data, ctx, offset=1, limit=1).items[0].event_id != page.items[0].event_id
    assert audit(data, ctx, decision="rejected").items == [second]
    assert audit(data, ctx, actor_id=ctx.user_id, since=first.timestamp, until=second.timestamp).total == 2
    data.add_member(ctx, contract_harness.member.username)
    expect_error("OWNER_REQUIRED", lambda: audit(data, replace(ctx, user_id=contract_harness.member.user_id)))


@pytest.mark.parametrize(
    "operation", ["create_project", "import_paper", "complete_job", "save_report", "append_audit"]
)
def test_commit_failure_leaves_no_partial_state(contract_harness, data, ctx, operation):
    if operation == "create_project":
        before = data.projects(ctx.user_id)

        def action():
            return data.create_project(ctx.user_id, ProjectCreate(name="应回滚的项目"), uuid4())
    elif operation == "import_paper":
        file = FileReference("rollback.pdf", uuid4(), ctx.project_id)

        def action():
            return import_paper(data, ctx, file=file, key="rollback-import")
    elif operation == "complete_job":
        paper, task = import_paper(data, ctx)
        job = data.claim_job()
        result = processing_result(paper.paper_id)

        def action():
            return data.complete_job(job, result)
    elif operation == "save_report":
        _, _, result = ready_paper(data, ctx)
        first = data.save_report(ctx, report_input(result), "first")

        def action():
            return data.save_report(
                ctx,
                report_input(result, report_id=first.report_id, base_version_id=first.version_id),
                "rollback-version",
            )
    else:
        entry = event(ctx)

        def action():
            return data.append_audit(entry)

    with contract_harness.failing_commit(operation):
        expect_error("DATA_UNAVAILABLE", action)
    if operation == "create_project":
        assert data.projects(ctx.user_id) == before
    elif operation == "import_paper":
        assert data.papers(ctx) == [] and data.claim_job() is None
        assert action()[0].paper_id == file.paper_id
    elif operation == "complete_job":
        assert data.sources(ctx, paper.paper_id) == []
        assert data.paper(ctx, paper.paper_id).status == "processing"
        assert data.task(ctx, task.task_id).status == TaskStatus.processing
        action()
        assert len(data.sources(ctx, paper.paper_id)) == 1
    elif operation == "save_report":
        assert len(data.versions(ctx, first.report_id)) == 1
        assert action().number == 2
    else:
        assert audit(data, ctx).total == 0
        action()
        assert audit(data, ctx).total == 1
