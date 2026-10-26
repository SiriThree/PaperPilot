"""Structured C behavior only; no model-quality or production index claims."""

import asyncio

import pytest

from app.errors import AppError
from app.schemas import (
    AnswerResult,
    DraftResult,
    EvidenceResult,
    ProcessResult,
    QuestionRequest,
    SearchResult,
)


def run(value):
    return asyncio.run(value)


def provenance(result, harness):
    assert result.mock is harness.is_mock


def test_process_returns_sources_without_committing_data(ai_harness):
    h = ai_harness
    before = h.data.sources(h.ctx, h.file.paper_id)
    result = ProcessResult.model_validate(run(h.ai.process(h.ctx, h.task_id, h.file)))
    provenance(result, h)
    assert result.paper_id == h.file.paper_id and result.sources
    assert len({s.chunk_id for s in result.sources}) == len(result.sources)
    assert len({s.citation_id for s in result.sources}) == len(result.sources)
    assert all(
        s.paper_id == result.paper_id and s.page_number >= 1 and s.excerpt.strip() for s in result.sources
    )
    assert h.data.sources(h.ctx, h.file.paper_id) == before


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_search_modes_scope_and_canonical_sources(ai_harness, mode):
    h = ai_harness
    query = h.query.model_copy(update={"mode": mode})
    result = SearchResult.model_validate(run(h.ai.search(h.ctx, query)))
    provenance(result, h)
    assert result.mode == mode and result.items and len(result.items) <= query.limit
    assert len({hit.source.citation_id for hit in result.items}) == len(result.items)
    for hit in result.items:
        assert hit.paper == h.data.paper(h.ctx, hit.paper.paper_id)
        assert hit.source.paper_id == hit.paper.paper_id
        assert hit.paper.status == "searchable"
        assert any(
            s.chunk_id == hit.source.chunk_id
            and s.excerpt == hit.source.excerpt
            and s.page_number == hit.source.page_number
            and s.synthetic == hit.source.synthetic
            for s in h.data.sources(h.ctx, hit.paper.paper_id)
        )


def test_search_year_limit_and_other_project(ai_harness):
    h = ai_harness
    own = run(h.ai.search(h.ctx, h.query))
    other = run(h.ai.search(h.other_ctx, h.query))
    assert not ({hit.paper.paper_id for hit in own.items} & {hit.paper.paper_id for hit in other.items})
    query = h.query.model_copy(update={"year_from": 2025, "year_to": 2025, "limit": 1})
    result = SearchResult.model_validate(run(h.ai.search(h.ctx, query)))
    assert len(result.items) <= 1
    assert all(hit.paper.year == 2025 for hit in result.items)


def test_answer_only_uses_provided_citations(ai_harness):
    h = ai_harness
    result = AnswerResult.model_validate(
        run(h.ai.answer(h.ctx, QuestionRequest(question=h.query.query), [h.source]))
    )
    provenance(result, h)
    assert result.answer.strip() and not result.insufficient_evidence
    assert result.sources and all(source == h.source for source in result.sources)
    assert len(result.citation_ids) == len(set(result.citation_ids))
    assert set(result.citation_ids) == {source.citation_id for source in result.sources}


def test_answer_without_sources_reports_insufficient_evidence(ai_harness):
    h = ai_harness
    result = AnswerResult.model_validate(run(h.ai.answer(h.ctx, QuestionRequest(question=h.query.query), [])))
    assert result.insufficient_evidence and not result.sources and not result.citation_ids


def test_extract_fields_and_source_provenance(ai_harness):
    h = ai_harness
    result = EvidenceResult.model_validate(run(h.ai.extract(h.ctx, h.source.paper_id, [h.source])))
    provenance(result, h)
    assert result.paper_id == h.source.paper_id
    for name in ("research_question", "method", "results", "conclusion", "limitations"):
        field = getattr(result, name)
        assert all(source == h.source for source in field.sources)
        if field.status == "pending_verification":
            assert field.text and field.sources


def test_draft_returns_body_not_a_saved_report(ai_harness):
    h = ai_harness
    before = h.data.reports(h.ctx)
    result = DraftResult.model_validate(
        run(h.ai.draft(h.ctx, QuestionRequest(question=h.query.query), [h.source]))
    )
    provenance(result, h)
    assert result.body.strip() and all(source == h.source for source in result.sources)
    assert h.data.reports(h.ctx) == before


def test_configured_ai_failure_is_a_safe_error(ai_harness):
    h = ai_harness
    with pytest.raises(AppError) as error:
        run(h.failing_ai.answer(h.ctx, QuestionRequest(question=h.failure_question), [h.source]))
    assert error.value.status in (502, 503, 504) and error.value.code and error.value.message
