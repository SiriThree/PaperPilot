"""Business selection checks using the existing in-memory adapters."""

from uuid import UUID

from conftest import finished
from app.errors import AppError


def test_evidence_budget_order_and_empty_sources(signed, project, app, monkeypatch):
    base = f"/api/v1/projects/{project}"
    paper = signed.get(base + "/papers").json()["items"][0]["paper_id"]
    original = app.state.adapters.data.sources
    captured = []
    empty = False
    excerpt_size = 4000

    def sources(ctx, paper_id):
        if str(paper_id) != paper:
            return original(ctx, paper_id)
        if empty:
            return []
        source = original(ctx, paper_id)[0]
        return [
            source.model_copy(
                update={"chunk_id": UUID(int=i), "page_number": 1, "excerpt": "x" * excerpt_size}
            )
            for i in range(60, 0, -1)
        ]

    original_extract = app.state.adapters.ai.extract

    async def extract(ctx, paper_id, selected):
        captured.append(selected)
        return await original_extract(ctx, paper_id, selected)

    async def forbidden(*args):
        raise AssertionError("global search used for evidence")

    monkeypatch.setattr(app.state.adapters.data, "sources", sources)
    monkeypatch.setattr(app.state.adapters.ai, "extract", extract)
    monkeypatch.setattr(app.state.service, "search", forbidden)
    task = signed.post(base + f"/papers/{paper}/evidence").json()["task_id"]
    result = finished(signed, project, task)
    assert result["status"] == "succeeded"
    assert len(captured[-1]) == 15
    assert [s.chunk_id.int for s in captured[-1]] == list(range(1, 16))
    assert [s.citation_id for s in captured[-1]] == [str(i) for i in range(1, 16)]
    assert result["result"]["method"]["sources"][0]["chunk_id"] == str(UUID(int=1))
    excerpt_size = 1
    task = signed.post(base + f"/papers/{paper}/evidence").json()["task_id"]
    assert finished(signed, project, task)["status"] == "succeeded"
    assert len(captured[-1]) == 50
    empty = True
    task = signed.post(base + f"/papers/{paper}/evidence").json()["task_id"]
    result = finished(signed, project, task)["result"]
    assert all(
        result[name]["status"] == "insufficient_evidence"
        for name in ("research_question", "method", "results", "conclusion", "limitations")
    )
    assert captured[-1] == []


def test_evidence_explicit_retry_reuses_task(signed, project, app, monkeypatch):
    base = f"/api/v1/projects/{project}"
    paper = signed.get(base + "/papers").json()["items"][0]["paper_id"]
    original = app.state.adapters.ai.extract
    calls = 0

    async def flaky(ctx, paper_id, sources):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AppError(503, "AI_UNAVAILABLE", "AI 服务暂不可用")
        return await original(ctx, paper_id, sources)

    monkeypatch.setattr(app.state.adapters.ai, "extract", flaky)
    task = signed.post(base + f"/papers/{paper}/evidence").json()["task_id"]
    assert finished(signed, project, task)["status"] == "failed"
    retried = signed.post(base + f"/tasks/{task}/retry")
    assert retried.status_code == 202 and retried.json()["task_id"] == task
    done = finished(signed, project, task)
    assert done["status"] == "succeeded" and done["attempts"] == 2
    assert calls == 2
