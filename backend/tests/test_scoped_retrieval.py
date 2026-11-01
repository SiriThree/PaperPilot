"""Selected-paper retrieval must filter before ranking and the top-k limit."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.adapters.mock_ai import MockAI
from app.adapters.mock_data import MockData, seed_id
from app.adapters.ports import Context
from app.ai.adapter import RealAI
from app.ai.files import LocalPDFReader
from app.errors import AppError
from app.schemas import Paper, QuestionRequest, SearchRequest, Source
from app.services import ResearchService
from ai_contracts.support import configuration, model_client


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def corpus():
    data = MockData()
    ctx = Context(seed_id("alice"), seed_id("project-a"), uuid4())
    data.paper_records.clear()
    data.source_records.clear()
    for i in range(60):
        pid = UUID(int=i + 1)
        data.paper_records[pid] = Paper(
            paper_id=pid,
            project_id=ctx.project_id,
            title="retrieval evidence",
            year=2026,
            status="searchable",
        )
        data.source_records[pid] = [
            Source(
                citation_id="1", paper_id=pid, chunk_id=uuid4(), page_number=1, excerpt="retrieval evidence"
            )
        ]
    return data, ctx, pid


@pytest.mark.anyio
@pytest.mark.parametrize("real", [False, True])
async def test_selected_paper_outside_project_top_50_is_retrieved(corpus, tmp_path, real):
    data, ctx, pid = corpus
    settings = replace(configuration(), max_candidates=1)
    ai = (
        RealAI(data, settings, reader=LocalPDFReader(tmp_path), client=model_client(settings))
        if real
        else MockAI(data)
    )
    service = ResearchService(SimpleNamespace(data=data, ai=ai))
    result = await service.search(ctx, SearchRequest(query="retrieval evidence", limit=50), paper_ids=[pid])
    assert len(result.items) == 1 and result.items[0].paper.paper_id == pid
    assert not (await service.search(ctx, SearchRequest(query="retrieval"), paper_ids=[])).items
    data.remove_paper(ctx, pid)
    with pytest.raises(AppError) as denied:
        await service.search(ctx, SearchRequest(query="retrieval"), paper_ids=[pid])
    assert denied.value.status == 404


@pytest.mark.anyio
async def test_unscoped_adapter_cannot_silently_degrade(corpus):
    data, ctx, pid = corpus
    service = ResearchService(SimpleNamespace(data=data, ai=SimpleNamespace(search=MockAI(data).search)))
    with pytest.raises(AppError) as unavailable:
        await service.search(ctx, SearchRequest(query="retrieval"), paper_ids=[pid])
    assert unavailable.value.code == "AI_SCOPE_UNAVAILABLE"


@pytest.mark.anyio
@pytest.mark.parametrize("real", [False, True])
@pytest.mark.parametrize("kind", ["answer", "draft"])
async def test_generation_job_keeps_selected_paper_outside_global_top_50(corpus, tmp_path, real, kind):
    data, ctx, pid = corpus
    settings = replace(configuration(), max_candidates=1)
    ai = (
        RealAI(data, settings, reader=LocalPDFReader(tmp_path), client=model_client(settings))
        if real
        else MockAI(data)
    )
    service = ResearchService(SimpleNamespace(data=data, ai=ai))
    task = service.question_task(
        ctx, kind, QuestionRequest(question="retrieval evidence", paper_ids=[pid]), uuid4().hex
    )
    job = data.claim_job()
    assert job.task.task_id == task.task_id
    await service.run_job(job)
    completed = data.task(ctx, task.task_id)
    assert completed.status == "succeeded"
    assert completed.result.sources
    assert {source.paper_id for source in completed.result.sources} == {pid}
