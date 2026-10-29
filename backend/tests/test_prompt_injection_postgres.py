"""C/D boundary regression with real PDF parsing and hostile model output; no live model scoring."""

import asyncio
import hashlib
import json
import os
from uuid import uuid4

import httpx
import pytest

from app.adapters.ports import FileReference
from app.ai.adapter import RealAI
from app.ai.files import LocalPDFReader
from app.errors import AppError
from app.schemas import QuestionRequest
from ai_contracts.support import configuration, model_client, model_response, pdf_bytes
from security_contracts.attack_cases import PROMPTS
from security_contracts.postgres_provider import postgres_workspace

pytestmark = pytest.mark.skipif(
    os.getenv("PAPERPILOT_SECURITY_CURRENT_SOURCE") != "1", reason="Explicit disposable PostgreSQL only"
)


@pytest.fixture(scope="module")
def pg():
    with postgres_workspace() as workspace:
        yield workspace


@pytest.mark.parametrize("origin,prompt", PROMPTS)
def test_untrusted_user_or_paper_cannot_expand_citations_or_database_access(pg, tmp_path, origin, prompt):
    ctx = pg.contexts[0]
    paper_id = uuid4()
    file = FileReference(f"{paper_id}.pdf", paper_id, ctx.project_id)
    payload = pdf_bytes(["Research evidence. " + (prompt if origin == "paper" else "Safe excerpt.")])
    (tmp_path / file.storage_key).write_bytes(payload)
    pg.data.import_paper(
        ctx,
        "Injection fixture",
        2026,
        hashlib.sha256(payload + paper_id.bytes).hexdigest(),
        file,
        uuid4().hex,
    )
    job = pg.data.claim_job()
    settings = configuration(embedding_revision="injection-fixture-v1")
    supplied = []

    def hostile_model(request):
        if request.url.path.endswith("/embeddings"):
            return model_response(request)
        body = json.loads(request.content)
        data = json.loads(body["messages"][1]["content"])
        assert "untrusted data" in body["messages"][0]["content"]
        assert "tools" not in body and "tool_choice" not in body
        supplied.extend(data["sources"])
        # Assume the model obeyed the attacker: a fabricated private citation must still fail closed.
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "insufficient_evidence": False,
                                    "statements": [
                                        {
                                            "text": "Read private data and execute DELETE FROM papers",
                                            "citation_ids": ["999"],
                                        }
                                    ],
                                }
                            )
                        },
                    }
                ]
            },
        )

    ai = RealAI(
        pg.data, settings, reader=LocalPDFReader(tmp_path), client=model_client(settings, hostile_model)
    )
    try:
        result, index = asyncio.run(ai.process_indexed(ctx, job.task.task_id, file))
        pg.data.complete_indexed_job(job, result, index)
        question = QuestionRequest(
            question=prompt if origin == "user" else "Summarize the evidence", paper_ids=[paper_id]
        )
        with pytest.raises(AppError) as rejected:
            asyncio.run(ai.answer(ctx, question, result.sources))
        assert rejected.value.code == "INVALID_CITATION"
        assert supplied and {source["paper_id"] for source in supplied} == {str(paper_id)}
        assert pg.data.paper(ctx, paper_id).title == "Injection fixture"
        with pytest.raises(AppError) as outside:
            pg.data.paper(ctx, pg.other.paper_id)
        assert outside.value.status == 404
    finally:
        pg.data.remove_paper(ctx, paper_id)
