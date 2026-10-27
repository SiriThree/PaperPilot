"""B reliability checks using isolated Mock ports, not real persistence certification."""

import asyncio
from pathlib import Path
from uuid import UUID

import pytest

from app.adapters.mock_data import seed_id
from app.adapters.ports import Context
from app.errors import AppError
from app.schemas import SearchHit, SearchRequest, SearchResult


@pytest.mark.parametrize("outcome", ["replay", "duplicate", "rollback"])
def test_cleanup_failure_preserves_known_import_outcome(app, signed, project, monkeypatch, caplog, outcome):
    base = f"/api/v1/projects/{project}/papers"
    kwargs = {
        "files": {"file": ("sample.pdf", b"%PDF-1.4\ncleanup test fixture")},
        "data": {"title": "cleanup fixture"},
        "headers": {"Idempotency-Key": "cleanup-first"},
    }
    original = signed.post(base, **kwargs)
    assert original.status_code == 202
    adopted = app.state.settings.storage_dir / f"{original.json()['paper']['paper_id']}.pdf"
    content = adopted.read_bytes()

    def cannot_unlink(path, *args, **kw):
        raise PermissionError("PRIVATE_STORAGE_PATH_AND_CREDENTIAL")

    monkeypatch.setattr(Path, "unlink", cannot_unlink)
    if outcome == "duplicate":
        kwargs["headers"] = {"Idempotency-Key": "cleanup-second"}
    if outcome == "rollback":

        def rollback(*args):
            raise AppError(503, "DATA_UNAVAILABLE", "confirmed rollback")

        monkeypatch.setattr(app.state.adapters.data, "import_paper", rollback)
    response = signed.post(base, **kwargs)
    assert response.status_code == {"replay": 202, "duplicate": 409, "rollback": 503}[outcome]
    if outcome == "replay":
        # A worker may advance status between these requests; identity must stay stable.
        assert response.json()["paper"]["paper_id"] == original.json()["paper"]["paper_id"]
        assert response.json()["paper"]["title"] == original.json()["paper"]["title"]
        assert response.json()["task"]["task_id"] == original.json()["task"]["task_id"]
    else:
        assert response.json()["code"] == (
            "DUPLICATE_PAPER" if outcome == "duplicate" else "DATA_UNAVAILABLE"
        )
    assert adopted.read_bytes() == content
    assert len(list(app.state.settings.storage_dir.glob("*.pdf"))) == 2
    assert "upload_cleanup_deferred" in caplog.text
    assert "PRIVATE_STORAGE_PATH_AND_CREDENTIAL" not in caplog.text + response.text
    assert str(app.state.settings.storage_dir) not in caplog.text


def context(project):
    return Context(user_id=seed_id("alice"), project_id=UUID(project), request_id=seed_id("request"))


def test_source_checks_read_once_per_paper_but_never_cache_between_requests(
    app, signed, project, monkeypatch
):
    data, service, ctx = app.state.adapters.data, app.state.service, context(project)
    sources = data.sources(ctx, seed_id("paper-a"))
    source = sources[0]
    references = [source.model_copy(update={"citation_id": str(i)}) for i in range(1, 4)]
    reads = []
    original = data.sources

    def counted(*args):
        reads.append(args)
        return original(*args)

    monkeypatch.setattr(data, "sources", counted)
    service.validate_sources(ctx, references, allowed=references)
    assert len(reads) == 1
    service.validate_sources(ctx, references, allowed=references)
    assert len(reads) == 2


@pytest.mark.parametrize("field,value", [("excerpt", "forged"), ("page_number", 999), ("synthetic", False)])
def test_batched_source_checks_still_reject_forged_references(app, signed, project, field, value):
    ctx = context(project)
    source = app.state.adapters.data.sources(ctx, seed_id("paper-a"))[0]
    forged = source.model_copy(update={field: value, "citation_id": "2"})
    with pytest.raises(AppError) as error:
        app.state.service.validate_sources(ctx, [source, forged])
    assert error.value.code == "INVALID_CITATION"


def test_search_validates_paper_metadata_once_per_unique_paper(app, signed, project, monkeypatch):
    data, service, ctx = app.state.adapters.data, app.state.service, context(project)
    paper = data.paper(ctx, seed_id("paper-a"))
    source = data.sources(ctx, paper.paper_id)[0]
    hits = [
        SearchHit(paper=paper, source=source.model_copy(update={"citation_id": str(i)})) for i in range(1, 4)
    ]
    reads = []
    original = data.paper

    def counted(*args):
        reads.append(args)
        return original(*args)

    async def search(ctx, query):
        return SearchResult(items=hits, mode=query.mode)

    monkeypatch.setattr(data, "paper", counted)
    monkeypatch.setattr(data, "sources", lambda *args: [source])
    monkeypatch.setattr(app.state.adapters.ai, "search", search)
    result = asyncio.run(service.search(ctx, SearchRequest(query="evidence")))
    assert result.items == hits
    assert len(reads) == 1
