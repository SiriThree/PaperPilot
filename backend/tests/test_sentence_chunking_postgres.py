"""Explicit disposable-PostgreSQL test of chunk-version history, not a quality benchmark."""

import asyncio
from dataclasses import replace
import hashlib
import os
from uuid import uuid4

import pytest

from test_sentence_chunking import make_tokenizer_directory
from ai_contracts.support import configuration, model_client, pdf_bytes
from app.adapters.ports import Context, FileReference
from app.ai.adapter import RealAI
from app.ai.files import LocalPDFReader
from app.schemas import ProjectCreate, SearchRequest

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        os.getenv("PAPERPILOT_CHUNKING_POSTGRES_TEST") != "1",
        reason="Requires explicit disposable PostgreSQL chunk-version test opt-in",
    ),
]


@pytest.fixture
def tokenizer_directory(tmp_path):
    return make_tokenizer_directory(tmp_path)


def test_rechunk_same_embedding_model_preserves_old_sources_and_replays_atomically(
    tokenizer_directory, tmp_path
):
    from app.data.provider import disposable_database
    from app.data.database import PostgresDatabase
    from app.data.adapter import PostgresData
    from app.data.settings import DataSettings

    with disposable_database("paperpilot_chunk_v2_test_") as config:
        database = PostgresDatabase(config)
        try:
            database.migrate()
            database.seed_development_users()
            data = PostgresData(database, DataSettings())
            alice = data.user_by_name("alice")
            project = data.create_project(
                alice.user_id, ProjectCreate(name="Chunk history protocol fixture"), uuid4()
            )
            ctx = Context(alice.user_id, project.project_id, uuid4())
            pid = uuid4()
            file = FileReference(f"{pid}.pdf", pid, ctx.project_id)
            payload = pdf_bytes(
                ["This is complete evidence. Another sentence preserves original text. " * 40]
            )
            (tmp_path / file.storage_key).write_bytes(payload)
            data.import_paper(
                ctx, "Evidence fixture", 2026, hashlib.sha256(payload).hexdigest(), file, uuid4().hex
            )
            job = data.claim_job()
            settings = configuration(
                embedding_backend="fastembed",
                local_model_dir=tokenizer_directory,
                embedding_revision="same-model-revision",
                chunk_strategy="character-v1",
                chunk_tokens=32,
                chunk_token_overlap=8,
            )
            client = model_client(settings)
            old_ai = RealAI(data, settings, reader=LocalPDFReader(tmp_path), client=client, embedder=client)
            old, old_index = asyncio.run(old_ai.process_indexed(ctx, job.task.task_id, file))
            data.complete_indexed_job(job, old, old_index)
            # Warm the derived lexical index before reindexing the same paper.
            assert asyncio.run(
                old_ai.search_scoped(ctx, SearchRequest(query="evidence", mode="keyword"), [pid])
            ).items
            with database.transaction() as c:
                old_payload = c.execute(
                    "SELECT payload FROM task_results WHERE task_id=%s", (job.task.task_id,)
                ).fetchone()["payload"]
                old_rows = c.execute(
                    "SELECT chunk_id,page_number,excerpt FROM chunks WHERE paper_id=%s ORDER BY chunk_id",
                    (pid,),
                ).fetchall()
            key = uuid4().hex
            task = data.reindex_paper(ctx, pid, key)
            assert data.reindex_paper(ctx, pid, key).task_id == task.task_id
            next_job = data.claim_job()
            new_ai = RealAI(
                data,
                replace(settings, chunk_strategy="sentence-token-v2"),
                reader=old_ai.reader,
                client=client,
                embedder=client,
            )
            new, new_index = asyncio.run(new_ai.process_indexed(ctx, next_job.task.task_id, file))
            assert new_index.model == old_index.model
            assert {s.chunk_id for s in new.sources}.isdisjoint(s.chunk_id for s in old.sources)
            data.complete_indexed_job(next_job, new, new_index)
            data.complete_indexed_job(next_job, new, new_index)
            assert data.sources(ctx, pid) == new.sources
            refreshed = asyncio.run(
                old_ai.search_scoped(ctx, SearchRequest(query="evidence", mode="keyword"), [pid])
            )
            assert refreshed.items and all(
                h.source.chunk_id in {s.chunk_id for s in new.sources} for h in refreshed.items
            )
            with database.transaction() as c:
                assert c.execute("SELECT count(*) AS n FROM embedding_models").fetchone()["n"] == 1
                assert c.execute("SELECT count(*) AS n FROM process_indexes").fetchone()["n"] == 2
                assert (
                    c.execute(
                        "SELECT payload FROM task_results WHERE task_id=%s", (job.task.task_id,)
                    ).fetchone()["payload"]
                    == old_payload
                )
                assert (
                    c.execute(
                        "SELECT chunk_id,page_number,excerpt FROM chunks WHERE chunk_id=ANY(%s) ORDER BY chunk_id",
                        ([s.chunk_id for s in old.sources],),
                    ).fetchall()
                    == old_rows
                )
            found = asyncio.run(
                new_ai.search_scoped(ctx, SearchRequest(query="evidence", mode="semantic"), [pid])
            )
            assert found.items and all(
                h.source.chunk_id in {s.chunk_id for s in new.sources} for h in found.items
            )
        finally:
            database.close()
