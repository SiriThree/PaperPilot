"""Current merged A/C index commits, failure fencing and restart reuse on PostgreSQL."""

import asyncio
import hashlib
import os
from dataclasses import replace
from uuid import uuid4

import pytest

from app.adapters.ports import FileReference
from app.ai.adapter import RealAI
from app.ai.files import LocalPDFReader
from app.errors import AppError
from app.schemas import SearchRequest
from ai_contracts.support import configuration, model_client, pdf_bytes
from security_contracts.postgres_provider import postgres_workspace

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        os.getenv("PAPERPILOT_SECURITY_CURRENT_SOURCE") != "1",
        reason="Requires merged checkout and disposable PostgreSQL",
    ),
]


@pytest.fixture(scope="module")
def pg():
    with postgres_workspace() as workspace:
        yield workspace


def prepare(pg, tmp_path):
    ctx = pg.contexts[0]
    pid = uuid4()
    file = FileReference(f"{pid}.pdf", pid, ctx.project_id)
    payload = pdf_bytes(["Retrieval evidence with a traceable source. Original fixture only."])
    (tmp_path / file.storage_key).write_bytes(payload)
    pg.data.import_paper(
        ctx, "Index fixture", 2026, hashlib.sha256(payload + pid.bytes).hexdigest(), file, uuid4().hex
    )
    job = pg.data.claim_job()
    assert job.payload["file"] == file
    settings = replace(configuration(), embedding_revision="fixture-v1")
    client = model_client(settings)
    ai = RealAI(pg.data, settings, reader=LocalPDFReader(tmp_path), client=client)
    result, index = asyncio.run(ai.process_indexed(ctx, job.task.task_id, file))
    return job, result, index, ai


def test_atomic_commit_restart_and_model_version(pg, tmp_path):
    job, result, index, ai = prepare(pg, tmp_path)
    pg.data.complete_indexed_job(job, result, index)
    pg.data.complete_indexed_job(job, result, index)
    with pg.data.database.transaction() as conn:
        row = conn.execute(
            "SELECT pp.index_state,c.content,ce.dimensions FROM project_papers pp JOIN chunks c USING(project_id,paper_id) JOIN chunk_embeddings ce USING(project_id,chunk_id) WHERE pp.paper_id=%s",
            (result.paper_id,),
        ).fetchone()
        assert row == {"index_state": "ready", "content": result.sources[0].excerpt, "dimensions": 3}
    # A new pool + AI instance must load documents' stored vectors, embedding only the query.
    from app.data.adapter import PostgresData
    from app.data.database import PostgresDatabase

    restarted_db = PostgresDatabase(pg.config)
    restarted = PostgresData(restarted_db, pg.data.settings)
    calls = []

    class CountingClient:
        async def embed(self, texts):
            calls.append(texts)
            return await ai.client.embed(texts)

    new_ai = RealAI(restarted, ai.settings, reader=ai.reader, client=CountingClient())
    try:
        hits = asyncio.run(
            new_ai.search_scoped(
                job.context, SearchRequest(query="Retrieval evidence", mode="semantic"), [result.paper_id]
            )
        )
        assert hits.items and calls == [["Retrieval evidence"]]
        changed = RealAI(
            restarted,
            replace(ai.settings, embedding_revision="fixture-v2"),
            reader=ai.reader,
            client=CountingClient(),
        )
        with pytest.raises(AppError) as missing:
            asyncio.run(
                changed.search_scoped(
                    job.context, SearchRequest(query="evidence", mode="semantic"), [result.paper_id]
                )
            )
        assert missing.value.code == "INDEX_NOT_READY"
        assert asyncio.run(
            changed.search_scoped(
                job.context, SearchRequest(query="evidence", mode="keyword"), [result.paper_id]
            )
        ).items
    finally:
        restarted_db.close()
    changed_index = index.model_copy(deep=True)
    changed_index.model = index.model.model_copy(update={"revision": "fixture-v2"})
    with pytest.raises(AppError) as conflict:
        pg.data.complete_indexed_job(job, result, changed_index)
    assert conflict.value.code == "TASK_STATE_CONFLICT"


def test_rollback_and_stale_attempt_cannot_publish_index(pg, tmp_path):
    job, result, index, _ = prepare(pg, tmp_path)

    def fail(operation, conn):
        if operation == "complete_indexed_job":
            raise RuntimeError("fixture rollback")

    pg.data._before_commit = fail
    try:
        with pytest.raises(RuntimeError):
            pg.data.complete_indexed_job(job, result, index)
    finally:
        pg.data._before_commit = None
    assert pg.data.task(job.context, job.task.task_id).status == "processing"
    with pg.data.database.transaction(write=True) as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM chunks WHERE paper_id=%s", (result.paper_id,)).fetchone()[
                "n"
            ]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM process_indexes WHERE task_id=%s", (job.task.task_id,)
            ).fetchone()["n"]
            == 0
        )
        conn.execute(
            "UPDATE task_attempts SET claimed_at=now()-interval '2 seconds', lease_expires_at=now()-interval '1 second' WHERE task_id=%s",
            (job.task.task_id,),
        )
    newer = pg.data.claim_job()
    assert newer.task.attempts == job.task.attempts + 1
    with pytest.raises(AppError) as stale:
        pg.data.complete_indexed_job(job, result, index)
    assert stale.value.code == "TASK_STATE_CONFLICT"
    pg.data.complete_indexed_job(newer, result, index)


def test_model_dimensions_conflict_rolls_back(pg, tmp_path):
    job, result, index, _ = prepare(pg, tmp_path)
    index.model = index.model.model_copy(update={"dimensions": 2})
    for chunk in index.chunks:
        chunk.vector = [1.0, 0.0]
    with pytest.raises(AppError) as conflict:
        pg.data.complete_indexed_job(job, result, index)
    assert conflict.value.code == "INDEX_MODEL_CONFLICT"
    assert not pg.data.sources(job.context, result.paper_id)
    pg.data.fail_job(job, "INDEX_MODEL_CONFLICT", "fixture dimension conflict")
