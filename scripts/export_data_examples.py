"""Executed Mock examples for every internal DataPort method; never connects to a DB."""

import inspect
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.adapters.data_rules import canonical  # noqa: E402
from app.adapters.mock_data import MockData  # noqa: E402
from app.adapters.ports import Context, DataPort, FileReference  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.schemas import (  # noqa: E402
    AnswerResult,
    AuditEvent,
    EvidenceField,
    EvidenceResult,
    ProcessResult,
    ProjectCreate,
    QuestionRequest,
    ReportSave,
    Source,
)


def export():
    data = MockData()
    alice, bob, eve = (data.user_by_name(name) for name in ("alice", "bob", "eve"))
    examples = {}

    def record(name, inputs, call, negative_inputs, negative):
        result = call()
        try:
            absent = {"input": negative_inputs, "output": negative()}
        except AppError as exc:
            absent = {
                "input": negative_inputs,
                "error": {"status": exc.status, "code": exc.code, "message": exc.message},
            }
        examples[name] = {
            "owner": "A",
            "signature": str(inspect.signature(getattr(DataPort, name))),
            "success": {"input": inputs, "output": result},
            "missing_or_error": absent,
        }
        return result

    missing = uuid4()
    record(
        "user_by_name",
        {"username": alice.username},
        lambda: data.user_by_name(alice.username),
        {"username": "absent"},
        lambda: data.user_by_name("absent"),
    )
    record(
        "user_by_id",
        {"user_id": alice.user_id},
        lambda: data.user_by_id(alice.user_id),
        {"user_id": missing},
        lambda: data.user_by_id(missing),
    )
    request_id = uuid4()
    project_data = ProjectCreate(name="A/B 交接示例", description="合成接口数据")
    project = record(
        "create_project",
        {"user_id": alice.user_id, "data": project_data, "request_id": request_id},
        lambda: data.create_project(alice.user_id, project_data, request_id),
        {"user_id": missing, "data": project_data, "request_id": request_id},
        lambda: data.create_project(missing, project_data, request_id),
    )
    ctx = Context(alice.user_id, project.project_id, request_id)
    denied = Context(eve.user_id, project.project_id, uuid4())
    record(
        "role",
        {"user_id": alice.user_id, "project_id": ctx.project_id},
        lambda: data.role(alice.user_id, ctx.project_id),
        {"user_id": eve.user_id, "project_id": ctx.project_id},
        lambda: data.role(eve.user_id, ctx.project_id),
    )
    record(
        "projects",
        {"user_id": alice.user_id},
        lambda: data.projects(alice.user_id),
        {"user_id": missing},
        lambda: data.projects(missing),
    )
    record("members", {"ctx": ctx}, lambda: data.members(ctx), {"ctx": denied}, lambda: data.members(denied))
    record(
        "add_member",
        {"ctx": ctx, "username": bob.username},
        lambda: data.add_member(ctx, bob.username),
        {"ctx": ctx, "username": "absent"},
        lambda: data.add_member(ctx, "absent"),
    )
    record(
        "remove_member",
        {"ctx": ctx, "user_id": bob.user_id},
        lambda: data.remove_member(ctx, bob.user_id),
        {"ctx": ctx, "user_id": alice.user_id},
        lambda: data.remove_member(ctx, alice.user_id),
    )
    file = FileReference("example.pdf", uuid4(), ctx.project_id)
    import_args = dict(
        ctx=ctx, title="合成接口论文", year=2024, digest="a" * 64, file=file, key="example-import"
    )
    paper, task = record(
        "import_paper",
        import_args,
        lambda: data.import_paper(**import_args),
        {**import_args, "key": "duplicate"},
        lambda: data.import_paper(**{**import_args, "key": "duplicate"}),
    )
    record(
        "papers",
        {"ctx": ctx, "year_from": 2024, "year_to": 2024},
        lambda: data.papers(ctx, 2024, 2024),
        {"ctx": denied},
        lambda: data.papers(denied),
    )
    record(
        "paper",
        {"ctx": ctx, "paper_id": paper.paper_id},
        lambda: data.paper(ctx, paper.paper_id),
        {"ctx": ctx, "paper_id": missing},
        lambda: data.paper(ctx, missing),
    )
    record(
        "task",
        {"ctx": ctx, "task_id": task.task_id},
        lambda: data.task(ctx, task.task_id),
        {"ctx": ctx, "task_id": missing},
        lambda: data.task(ctx, missing),
    )
    job = record("claim_job", {}, data.claim_job, {}, data.claim_job)
    source = Source(
        citation_id="1",
        paper_id=paper.paper_id,
        chunk_id=uuid4(),
        page_number=1,
        excerpt="【合成】数据层交接示例片段",
    )
    result = ProcessResult(paper_id=paper.paper_id, sources=[source])
    bad = ProcessResult(paper_id=missing, sources=[source])
    # Capture invalid completion first while the same attempt is still processing.
    try:
        data.complete_job(job, bad)
    except AppError as exc:
        completion_error = {
            "input": {"job": job, "result": bad},
            "error": {"status": exc.status, "code": exc.code, "message": exc.message},
        }
    record(
        "complete_job",
        {"job": job, "result": result},
        lambda: data.complete_job(job, result),
        {"job": job, "result": bad},
        lambda: data.complete_job(job, bad),
    )
    examples["complete_job"]["missing_or_error"] = completion_error
    record(
        "sources",
        {"ctx": ctx, "paper_id": paper.paper_id},
        lambda: data.sources(ctx, paper.paper_id),
        {"ctx": denied, "paper_id": paper.paper_id},
        lambda: data.sources(denied, paper.paper_id),
    )
    payload = {
        "question": QuestionRequest(question="合成问题", paper_ids=[paper.paper_id]),
        "paper_ids": [paper.paper_id],
    }
    research = record(
        "create_task",
        {"ctx": ctx, "kind": "answer", "payload": payload, "key": "answer-example"},
        lambda: data.create_task(ctx, "answer", payload, "answer-example"),
        {"ctx": denied, "kind": "answer", "payload": payload, "key": "denied"},
        lambda: data.create_task(denied, "answer", payload, "denied"),
    )
    research_job = data.claim_job()
    record(
        "fail_job",
        {"job": research_job, "code": "AI_UNAVAILABLE", "message": "合成失败"},
        lambda: data.fail_job(research_job, "AI_UNAVAILABLE", "合成失败"),
        {"job": job, "code": "AI_UNAVAILABLE", "message": "迟到失败"},
        lambda: data.fail_job(job, "AI_UNAVAILABLE", "迟到失败"),
    )
    record(
        "retry_task",
        {"ctx": ctx, "task_id": research.task_id},
        lambda: data.retry_task(ctx, research.task_id),
        {"ctx": ctx, "task_id": research.task_id},
        lambda: data.retry_task(ctx, research.task_id),
    )
    retried = data.claim_job()
    data.complete_job(
        retried, AnswerResult(answer="证据不足", citation_ids=[], sources=[], insufficient_evidence=True)
    )
    data.create_task(ctx, "evidence", payload, "evidence-example")
    evidence_job = data.claim_job()
    field = EvidenceField(text="合成证据", status="pending_verification", sources=[source])
    extraction = EvidenceResult(
        evidence_id=uuid4(),
        paper_id=paper.paper_id,
        research_question=field,
        method=field,
        results=field,
        conclusion=field,
        limitations=EvidenceField(text=None, status="not_mentioned", sources=[]),
    )
    data.complete_job(evidence_job, extraction)
    record(
        "evidence",
        {"ctx": ctx, "paper_id": paper.paper_id},
        lambda: data.evidence(ctx, paper.paper_id),
        {"ctx": ctx, "paper_id": missing},
        lambda: data.evidence(ctx, missing),
    )
    report = ReportSave(title="合成报告", research_question="合成问题", body="合成正文 [1]", sources=[source])
    version = record(
        "save_report",
        {"ctx": ctx, "data": report, "key": "report-example"},
        lambda: data.save_report(ctx, report, "report-example"),
        {"ctx": denied, "data": report, "key": "denied"},
        lambda: data.save_report(denied, report, "denied"),
    )
    record(
        "versions",
        {"ctx": ctx, "report_id": version.report_id},
        lambda: data.versions(ctx, version.report_id),
        {"ctx": ctx, "report_id": missing},
        lambda: data.versions(ctx, missing),
    )
    record("reports", {"ctx": ctx}, lambda: data.reports(ctx), {"ctx": denied}, lambda: data.reports(denied))
    record(
        "controlled_statistics",
        {"ctx": ctx, "intent": "by_year"},
        lambda: data.controlled_statistics(ctx, "by_year"),
        {"ctx": ctx, "intent": "arbitrary_sql"},
        lambda: data.controlled_statistics(ctx, "arbitrary_sql"),
    )
    entry = AuditEvent(
        event_id=uuid4(),
        timestamp=datetime.now(UTC),
        request_id=ctx.request_id,
        actor_id=ctx.user_id,
        project_id=ctx.project_id,
        action="example",
        resource_type="paper",
        decision="allowed",
        execution_status="completed",
        duration_ms=0,
    )
    record(
        "append_audit",
        {"event": entry},
        lambda: data.append_audit(entry),
        {"event": entry.model_copy(update={"action": "changed"})},
        lambda: data.append_audit(entry.model_copy(update={"action": "changed"})),
    )
    filters = dict(action=None, decision=None, actor_id=None, since=None, until=None, offset=0, limit=20)
    record(
        "audit",
        {"ctx": ctx, **filters},
        lambda: data.audit(ctx, **filters),
        {"ctx": denied, **filters},
        lambda: data.audit(denied, **filters),
    )
    record(
        "remove_paper",
        {"ctx": ctx, "paper_id": paper.paper_id},
        lambda: data.remove_paper(ctx, paper.paper_id),
        {"ctx": ctx, "paper_id": missing},
        lambda: data.remove_paper(ctx, missing),
    )
    expected = {name for name in DataPort.__dict__ if not name.startswith("_")}
    assert set(examples) == expected
    target = ROOT / "contracts/data-port-examples.json"
    target.write_text(
        json.dumps(
            json.loads(canonical({"contract": "AB-1", "mock": True, "methods": examples})),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Exported {len(examples)} DataPort examples; Mock only, no database")


if __name__ == "__main__":
    export()
