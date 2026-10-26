from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from app.adapters.ports import FileReference
from app.schemas import (
    AuditEvent,
    EvidenceField,
    EvidenceResult,
    ProcessResult,
    QuestionRequest,
    ReportSave,
    Source,
)


def import_paper(data, ctx, *, text="fixture", key=None, file=None):
    file = file or FileReference(f"{uuid4()}.pdf", uuid4(), ctx.project_id)
    return data.import_paper(
        ctx, "合成契约论文", 2024, sha256(text.encode()).hexdigest(), file, key or str(uuid4())
    )


def processing_result(paper_id):
    return ProcessResult(
        paper_id=paper_id,
        sources=[
            Source(
                citation_id="1",
                paper_id=paper_id,
                chunk_id=uuid4(),
                page_number=2,
                excerpt="【合成】适配器契约测试来源",
            )
        ],
    )


def ready_paper(data, ctx):
    paper, task = import_paper(data, ctx)
    job = data.claim_job()
    assert job.task.task_id == task.task_id
    result = processing_result(paper.paper_id)
    data.complete_job(job, result)
    return paper, task, result


def report_input(result, **changes):
    return ReportSave(
        title="契约报告", research_question="契约问题", body="版本一 [1]", sources=result.sources, **changes
    )


def research_payload(paper_id):
    return {"question": QuestionRequest(question="契约问题", paper_ids=[paper_id]), "paper_ids": [paper_id]}


def evidence_result(result):
    field = EvidenceField(text="合成证据", status="pending_verification", sources=result.sources)
    return EvidenceResult(
        evidence_id=uuid4(),
        paper_id=result.paper_id,
        research_question=field,
        method=field,
        results=field,
        conclusion=field,
        limitations=EvidenceField(text=None, status="not_mentioned", sources=[]),
    )


def event(ctx, **changes):
    payload = dict(
        event_id=uuid4(),
        timestamp=datetime.now(UTC),
        request_id=ctx.request_id,
        actor_id=ctx.user_id,
        project_id=ctx.project_id,
        action="contract.test",
        resource_type="paper",
        decision="allowed",
        execution_status="completed",
        duration_ms=0,
    )
    payload.update(changes)
    return AuditEvent(**payload)


def audit(data, ctx, **changes):
    query = dict(
        action="contract.test", decision=None, actor_id=None, since=None, until=None, offset=0, limit=20
    )
    query.update(changes)
    return data.audit(ctx, **query)
