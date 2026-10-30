"""Disposable database protocol fixtures; real-paper measurements live in demo/.

The 1,207 tiny fragments exercise the old cutoff and exact score/scope behavior.
They are never presented as a real literature collection or quality benchmark.
"""

import asyncio
from dataclasses import replace
import hashlib
import os
from uuid import uuid4

import pytest

from ai_contracts.support import configuration, model_client
from app.adapters.ports import Context, FileReference
from app.ai.adapter import RealAI
from app.ai.files import LocalPDFReader
from app.errors import AppError
from app.indexing import EmbeddingModel, IndexedChunk, ProcessIndex
from app.schemas import ProcessResult, ProjectCreate, SearchRequest, Source

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        os.getenv("PAPERPILOT_RETRIEVAL_POSTGRES_TEST") != "1",
        reason="Requires explicit disposable PostgreSQL retrieval test opt-in",
    ),
]


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    from app.data.provider import disposable_database
    from app.data.database import PostgresDatabase
    from app.data.adapter import PostgresData
    from app.data.settings import DataSettings

    with disposable_database("paperpilot_retrieval_test_") as config:
        db = PostgresDatabase(config)
        try:
            db.migrate()
            db.seed_development_users()
            data = PostgresData(db, DataSettings())
            user = data.user_by_name("alice")
            project = data.create_project(user.user_id, ProjectCreate(name="Retrieval fixture"), uuid4())
            other = data.create_project(user.user_id, ProjectCreate(name="Other fixture"), uuid4())
            ctx = Context(user.user_id, project.project_id, uuid4())
            foreign = replace(ctx, project_id=other.project_id)
            model = EmbeddingModel(
                provider="https://model.invalid/v1",
                model_name="fixture-embedding",
                revision="fixture-v1",
                dimensions=3,
            )

            def add(context, year, texts, vectors):
                pid = uuid4()
                file = FileReference(f"{pid}.pdf", pid, context.project_id)
                data.import_paper(
                    context,
                    "Protocol fixture",
                    year,
                    hashlib.sha256(pid.bytes).hexdigest(),
                    file,
                    uuid4().hex,
                )
                job = data.claim_job()
                sources = [
                    Source(
                        citation_id=str(i + 1),
                        paper_id=pid,
                        chunk_id=uuid4(),
                        page_number=1,
                        excerpt=text,
                        synthetic=False,
                    )
                    for i, text in enumerate(texts)
                ]
                result = ProcessResult(paper_id=pid, sources=sources, mock=False)
                index = ProcessIndex(
                    model=model,
                    chunks=[
                        IndexedChunk(chunk_id=s.chunk_id, content=s.excerpt, vector=v)
                        for s, v in zip(sources, vectors, strict=True)
                    ],
                )
                data.complete_indexed_job(job, result, index)
                return pid, sources

            pid, sources = add(
                ctx,
                2024,
                [f"Common filler {i}." for i in range(1206)] + ["raretarget 检索增强"],
                [[0.0, 1.0, 0.0]] * 1206 + [[1.0, 0.0, 0.0]],
            )
            foreign_pid, _ = add(foreign, 2024, ["raretarget"], [[1.0, 0.0, 0.0]])
            late_pid, _ = add(ctx, 2025, ["raretarget"], [[1.0, 0.0, 0.0]])
            unknown_year_pid, _ = add(ctx, None, ["raretarget"], [[1.0, 0.0, 0.0]])
            yield data, db, ctx, foreign, pid, sources, foreign_pid, late_pid, unknown_year_pid, model
        finally:
            db.close()


class QueryEmbeddings:
    def __init__(self, dimensions=3):
        self.inputs = []
        self.dimensions = dimensions

    async def embed(self, texts):
        self.inputs.extend(texts)
        assert len(texts) == 1  # Stored document vectors must never be re-embedded.
        return [[1.0] + [0.0] * (self.dimensions - 1)]


def adapter(workspace, tmp_path, **changes):
    data, *_, model = workspace
    settings = configuration(embedding_revision=model.revision, max_candidates=7, **changes)
    embedder = QueryEmbeddings(settings.embedding_dimensions)
    ai = RealAI(
        data, settings, reader=LocalPDFReader(tmp_path), client=model_client(settings), embedder=embedder
    )
    ai._corpus = lambda *args: pytest.fail("The bounded legacy corpus must not be loaded")
    return ai, embedder


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_searches_beyond_1000_and_scopes_before_ranking(workspace, tmp_path, mode):
    data, _, ctx, _, pid, sources, *_ = workspace
    ai, embedder = adapter(workspace, tmp_path)
    result = asyncio.run(
        ai.search(
            ctx,
            SearchRequest(
                query="raretarget",
                mode=mode,
                limit=10,
                year_from=2024,
                year_to=2024,
            ),
        )
    )
    assert len(result.items) == 1
    hit = result.items[0]
    assert hit.paper.paper_id == pid and hit.source.chunk_id == sources[-1].chunk_id
    assert hit.source.excerpt == sources[-1].excerpt
    assert hit.source.citation_id == "1"
    assert embedder.inputs == ([] if mode == "keyword" else ["raretarget"])
    # This scope still contains all 1,207 sources even though the shortlist budget is seven.
    assert len(data.sources(ctx, pid)) == 1207


def test_sql_cosine_matches_python_and_has_stable_ties(workspace):
    data, _, ctx, _, pid, sources, *_, model = workspace
    query = SearchRequest(query="common", year_from=2024, year_to=2024)
    with data.retrieval_snapshot(ctx, query) as snapshot:
        ranked = snapshot.nearest(model, [1.0, 0.0, 0.0], -0.5, 10)
    expected = sorted(
        [((pid, 1, s.chunk_id), 1.0 if s == sources[-1] else 0.0) for s in sources],
        key=lambda item: (-item[1], item[0]),
    )[:10]
    assert ranked == expected


@pytest.mark.parametrize(
    "changes",
    [
        {"embedding_model": "unindexed-model"},
        {"embedding_dimensions": 4},
    ],
)
def test_model_mismatch_fails_but_keywords_remain_available(workspace, tmp_path, changes):
    ai, _ = adapter(workspace, tmp_path, **changes)
    ctx = workspace[2]
    for mode in ("semantic", "hybrid"):
        with pytest.raises(AppError) as error:
            asyncio.run(ai.search(ctx, SearchRequest(query="raretarget", mode=mode)))
        assert error.value.code == "INDEX_NOT_READY"
    assert asyncio.run(ai.search(ctx, SearchRequest(query="raretarget", mode="keyword"))).items


def test_empty_year_range_and_blank_query_are_empty(workspace, tmp_path):
    ai, embedder = adapter(workspace, tmp_path)
    assert not asyncio.run(
        ai.search(
            workspace[2],
            SearchRequest(
                query="raretarget",
                mode="semantic",
                year_to=2020,
            ),
        )
    ).items
    embedder.inputs.clear()
    assert not asyncio.run(ai.search(workspace[2], SearchRequest(query="   ", mode="hybrid"))).items
    assert not embedder.inputs


def test_snapshot_timeout_is_reported_and_pool_remains_usable(workspace):
    data, _, ctx, *_ = workspace
    with pytest.raises(AppError) as error:
        with data.retrieval_snapshot(ctx, SearchRequest(query="common")) as snapshot:
            snapshot.deadline = 0
            list(snapshot.documents())
    assert error.value.code == "AI_TIMEOUT"
    assert data.paper(ctx, workspace[4]).status == "searchable"


def test_database_statement_timeout_is_safe_and_pool_recovers(workspace):
    data, _, ctx, *_ = workspace
    with pytest.raises(AppError) as error:
        with data.retrieval_snapshot(ctx, SearchRequest(query="common")) as snapshot:
            snapshot.connection.execute("SELECT set_config('statement_timeout','1',true)")
            snapshot.connection.execute("SELECT pg_sleep(0.05)")
    assert error.value.code == "AI_TIMEOUT"
    assert data.paper(ctx, workspace[4]).status == "searchable"


def test_membership_revoked_during_embedding_is_rechecked(workspace, tmp_path):
    data, _, owner, *_ = workspace
    data.add_member(owner, "bob")
    bob = replace(owner, user_id=data.user_by_name("bob").user_id)
    ai, _ = adapter(workspace, tmp_path)

    class RevokeDuringModelWait:
        async def embed(self, texts):
            data.remove_member(owner, bob.user_id)
            return [[1.0, 0.0, 0.0]]

    ai.embedder = RevokeDuringModelWait()
    with pytest.raises(AppError) as error:
        asyncio.run(ai.search(bob, SearchRequest(query="raretarget", mode="semantic")))
    assert error.value.status == 404


def test_selected_empty_and_foreign_scopes_and_revoked_membership(workspace, tmp_path):
    data, _, ctx, _, pid, _, foreign_pid, *_ = workspace
    ai, embedder = adapter(workspace, tmp_path)
    query = SearchRequest(query="raretarget", mode="semantic")
    assert not asyncio.run(ai.search_scoped(ctx, query, [])).items
    assert not embedder.inputs
    for scope in ([foreign_pid], [pid, uuid4()]):
        with pytest.raises(AppError) as error:
            asyncio.run(ai.search_scoped(ctx, query, scope))
        assert error.value.status == 404
    assert not embedder.inputs
    outsider = replace(ctx, user_id=data.user_by_name("eve").user_id)
    with pytest.raises(AppError) as error:
        asyncio.run(ai.search(outsider, query))
    assert error.value.status == 404 and not embedder.inputs


def test_read_snapshot_excludes_concurrent_import(workspace):
    data, db, ctx, *_ = workspace
    query = SearchRequest(query="common")
    with data.retrieval_snapshot(ctx, query) as snapshot:
        before = {key for key, _ in snapshot.documents()}
        # Snapshot is stable across both BM25 scans, even after a concurrent soft removal.
        target = workspace[7]
        with db.transaction(write=True) as conn:
            conn.execute("UPDATE project_papers SET removed_at=now() WHERE paper_id=%s", (target,))
        assert {key for key, _ in snapshot.documents()} == before
    with data.retrieval_snapshot(ctx, query) as snapshot:
        assert all(key[0] != target for key, _ in snapshot.documents())
