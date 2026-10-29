import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai.adapter import RealAI, build_ai
from app.ai.bootstrap import create_app as create_ai_app
from app.ai.files import LocalPDFReader
from app.ai.retrieval import bm25, fuse, tokens
from app.ai.settings import AISettings
from app.config import Settings
from app.errors import AppError
from app.main import create_app
from app.schemas import QuestionRequest, SearchRequest

from .real_provider import build_harness
from .support import configuration, model_client, model_response, pdf_bytes


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture
def real(tmp_path):
    with build_harness(tmp_path=tmp_path) as h:
        yield h


def test_pdf_pages_stable_chunks_and_no_commit(real):
    result = run(real.ai.process(real.ctx, real.task_id, real.file))
    again = run(real.ai.process(real.ctx, uuid4(), real.file))
    assert result == again and result.mock is False
    assert [s.page_number for s in result.sources] == [1, 2]
    assert "provenance" in result.sources[0].excerpt
    assert "Limitations" in result.sources[1].excerpt
    assert all(not s.synthetic for s in result.sources)
    assert all(s.synthetic for s in real.data.sources(real.ctx, real.file.paper_id))


@pytest.mark.parametrize(
    "payload,code",
    [
        (b"%PDF-1.7\nmalformed", "PDF_INVALID"),
        (pdf_bytes(["", ""]), "PDF_NO_TEXT"),
    ],
)
def test_invalid_or_scanned_pdf_is_explicit(real, tmp_path, payload, code):
    (tmp_path / real.file.storage_key).write_bytes(payload)
    with pytest.raises(AppError) as exc:
        run(real.ai.process(real.ctx, real.task_id, real.file))
    assert exc.value.code == code


def test_encrypted_pdf_is_explicit(real, tmp_path):
    import io
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("PRIVATE_PASSWORD")
    output = io.BytesIO()
    writer.write(output)
    (tmp_path / real.file.storage_key).write_bytes(output.getvalue())
    with pytest.raises(AppError) as exc:
        run(real.ai.process(real.ctx, real.task_id, real.file))
    assert exc.value.code == "PDF_ENCRYPTED" and "PRIVATE" not in exc.value.message


def test_long_page_chunks_overlap_without_crossing_page(real, tmp_path):
    text = "Retrieval evidence sentence. " * 200
    (tmp_path / real.file.storage_key).write_bytes(pdf_bytes(["", text]))
    result = run(real.ai.process(real.ctx, real.task_id, real.file))
    assert len(result.sources) > 1
    assert all(s.page_number == 2 and len(s.excerpt) <= 1200 for s in result.sources)
    assert len({s.chunk_id for s in result.sources}) == len(result.sources)


@pytest.mark.parametrize(
    "storage_key", ["../secret.pdf", "/etc/passwd", "https://example.com/x.pdf", "other.pdf"]
)
def test_only_bound_canonical_storage_keys(real, storage_key):
    with pytest.raises(AppError) as exc:
        run(real.ai.process(real.ctx, real.task_id, replace(real.file, storage_key=storage_key)))
    assert exc.value.code == "INVALID_FILE_REFERENCE"


def test_symlink_and_cross_project_files_are_not_opened(real, tmp_path):
    path = tmp_path / real.file.storage_key
    path.unlink()
    secret = tmp_path / "secret"
    secret.write_text("PRIVATE_FILE")
    path.symlink_to(secret)
    with pytest.raises(AppError) as exc:
        run(real.ai.process(real.ctx, real.task_id, real.file))
    assert exc.value.code == "FILE_UNAVAILABLE"
    with pytest.raises(AppError) as exc:
        run(real.ai.process(real.other_ctx, real.task_id, real.file))
    assert exc.value.status == 404


def test_keyword_is_real_ranking_and_does_not_call_models(real):
    def forbidden(request):
        raise AssertionError("keyword search must not call an API")

    real.ai.client = model_client(handler=forbidden)
    assert run(real.ai.search(real.ctx, SearchRequest(query="zzznomatch", mode="keyword"))).items == []
    result = run(real.ai.search(real.ctx, SearchRequest(query="检索增强生成", mode="keyword")))
    assert result.items and result.items[0].score > 0
    assert "检索增强" in result.items[0].source.excerpt
    assert "检索增强" in tokens("检索增强") or "检索" in tokens("检索增强")
    assert bm25("rare", ["rare", "irrelevant"])[0] > 0
    assert fuse([0, 1], [1])[1] > fuse([0, 1], [1])[0]


def test_fictional_extraction_keeps_provenance_and_rejects_invented_citations(real):
    original = real.data.sources(real.ctx, real.file.paper_id)[0]
    source = original.model_copy(
        update={"excerpt": "FICTIONAL example: simulated method compared twelve documents."}
    )
    real.data.source_records[real.file.paper_id] = [source]
    captured = []
    citation = [source.citation_id]

    def handler(request):
        body = json.loads(request.content)
        captured.append(body["messages"][0]["content"])
        payload = {
            name: {"text": None, "status": "not_mentioned", "citation_ids": []}
            for name in ("research_question", "method", "results", "conclusion", "limitations")
        }
        payload["method"] = {
            "text": "The fictional simulated example compared twelve documents.",
            "status": "pending_verification",
            "citation_ids": citation,
        }
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]}
        )

    real.ai.client = model_client(handler=handler)
    result = run(real.ai.extract(real.ctx, real.file.paper_id, [source]))
    assert "fictional or simulated" in captured[0]
    assert result.method.status == "pending_verification"
    assert "fictional" in result.method.text.lower()
    assert result.method.sources == [source]
    assert result.limitations.status == "not_mentioned" and not result.limitations.sources
    citation[:] = ["invented"]
    with pytest.raises(AppError) as exc:
        run(real.ai.extract(real.ctx, real.file.paper_id, [source]))
    assert exc.value.code == "INVALID_CITATION"


def test_semantic_uses_cosine_and_hybrid_fuses_rankings(real):
    # Make embeddings rank a lexically unrelated paper first; three paths must differ.
    from app.adapters.mock_data import seed_id
    from app.schemas import Paper, Source

    pid = seed_id("semantic-paper")
    real.data.paper_records[pid] = Paper(
        paper_id=pid, project_id=real.ctx.project_id, title="Paraphrase", status="searchable", year=2025
    )
    real.data.source_records[pid] = [
        Source(
            citation_id="x",
            paper_id=pid,
            chunk_id=uuid4(),
            page_number=2,
            excerpt="A paraphrase about grounding.",
            synthetic=False,
        )
    ]

    def embeddings(request):
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1, 0, 0] if i == 0 or "Paraphrase" in text else [0.3, 1, 0]}
                    for i, text in enumerate(body["input"])
                ]
            },
        )

    real.ai.client = model_client(handler=embeddings)
    lexical = run(real.ai.search(real.ctx, SearchRequest(query="检索增强", mode="keyword")))
    semantic = run(real.ai.search(real.ctx, SearchRequest(query="检索增强", mode="semantic")))
    hybrid = run(real.ai.search(real.ctx, SearchRequest(query="检索增强", mode="hybrid")))
    assert lexical.items[0].paper.paper_id == real.file.paper_id
    assert semantic.items[0].paper.paper_id == pid
    assert hybrid.items[0].paper.paper_id == real.file.paper_id
    assert semantic.items[0].score == 1


def test_project_is_filtered_before_provider_and_no_stale_index(real):
    requests = []

    def inspect(request):
        requests.append(request.content.decode())
        return model_response(request)

    real.ai.client = model_client(handler=inspect)
    run(real.ai.search(real.ctx, SearchRequest(query="检索增强")))
    assert requests and all("隔离项目合成论文" not in value for value in requests)
    real.data.remove_paper(real.ctx, real.file.paper_id)
    assert not run(real.ai.search(real.ctx, SearchRequest(query="检索增强"))).items


def test_membership_revocation_during_model_call_is_rechecked(real):
    def revoke(request):
        real.data.memberships.pop((real.ctx.project_id, real.ctx.user_id))
        return model_response(request)

    real.ai.client = model_client(handler=revoke)
    with pytest.raises(AppError) as exc:
        run(real.ai.answer(real.ctx, QuestionRequest(question="证据"), [real.source]))
    assert exc.value.status == 404


@pytest.mark.parametrize(
    "change",
    [
        {"excerpt": "forged"},
        {"synthetic": False},
        {"page_number": 9},
        {"chunk_id": uuid4()},
    ],
)
def test_forged_input_never_reaches_model(real, change):
    def forbidden(request):
        raise AssertionError("forged input must not reach provider")

    real.ai.client = model_client(handler=forbidden)
    with pytest.raises(AppError) as exc:
        run(
            real.ai.answer(
                real.ctx, QuestionRequest(question="证据"), [real.source.model_copy(update=change)]
            )
        )
    assert exc.value.code == "INVALID_CITATION"


@pytest.mark.parametrize(
    "result,code",
    [
        (
            {"insufficient_evidence": False, "statements": [{"text": "Invented", "citation_ids": ["999"]}]},
            "INVALID_CITATION",
        ),
        (
            {"insufficient_evidence": False, "statements": [{"text": "Forged [999]", "citation_ids": ["1"]}]},
            "AI_INVALID_RESPONSE",
        ),
        ({"insufficient_evidence": False, "statements": []}, "AI_INVALID_RESPONSE"),
        (
            {"insufficient_evidence": True, "statements": [{"text": "Contradiction", "citation_ids": ["1"]}]},
            "AI_INVALID_RESPONSE",
        ),
    ],
)
def test_model_cannot_invent_citations_or_claim_unsupported_success(real, result, code):
    def invalid(request):
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}
        )

    real.ai.client = model_client(handler=invalid)
    with pytest.raises(AppError) as exc:
        run(real.ai.answer(real.ctx, QuestionRequest(question="证据"), [real.source]))
    assert exc.value.code == code


def test_empty_evidence_never_calls_provider(real):
    def forbidden(request):
        raise AssertionError("no evidence must not call a model")

    real.ai.client = model_client(handler=forbidden)
    question = QuestionRequest(question="unknown")
    answer = run(real.ai.answer(real.ctx, question, []))
    draft = run(real.ai.draft(real.ctx, question, []))
    evidence = run(real.ai.extract(real.ctx, real.file.paper_id, []))
    assert answer.insufficient_evidence and not answer.mock and not answer.sources
    assert "证据不足" in draft.body and not draft.sources
    assert evidence.method.status == "insufficient_evidence" and evidence.method.text is None


@pytest.mark.parametrize(
    "failure,status,code",
    [
        ("timeout", 504, "AI_TIMEOUT"),
        ("connection", 503, "AI_UNAVAILABLE"),
        (429, 503, "AI_UNAVAILABLE"),
        (503, 503, "AI_UNAVAILABLE"),
        (401, 502, "AI_PROVIDER_ERROR"),
        ("malformed", 502, "AI_INVALID_RESPONSE"),
        ("refusal", 502, "AI_INVALID_RESPONSE"),
    ],
)
def test_provider_errors_are_safe(real, failure, status, code):
    def fail(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE_PROVIDER fixture-secret-not-a-key", request=request)
        if failure == "connection":
            raise httpx.ConnectError("PRIVATE_PROVIDER fixture-secret-not-a-key", request=request)
        if failure == "malformed":
            return httpx.Response(200, text="PRIVATE_PROVIDER fixture-secret-not-a-key")
        if failure == "refusal":
            return httpx.Response(
                200, json={"choices": [{"finish_reason": "stop", "message": {"refusal": "PRIVATE"}}]}
            )
        return httpx.Response(failure, text="PRIVATE_PROVIDER fixture-secret-not-a-key")

    real.ai.client = model_client(handler=fail)
    with pytest.raises(AppError) as exc:
        run(real.ai.answer(real.ctx, QuestionRequest(question="证据"), [real.source]))
    assert (exc.value.status, exc.value.code) == (status, code)
    assert "PRIVATE" not in exc.value.message and "secret" not in exc.value.message


@pytest.mark.parametrize("vector", [[0, 0, 0], [1, 0], [float("nan"), 0, 1], [True, 0, 1]])
def test_embedding_dimension_and_finite_values_are_enforced(vector):
    client = model_client(
        handler=lambda request: httpx.Response(200, json={"data": [{"index": 0, "embedding": vector}]})
    )
    with pytest.raises(AppError) as exc:
        run(client.embed(["text"]))
    assert exc.value.code == "AI_INVALID_RESPONSE"


def test_operation_timeout_and_cancellation(real):
    closed = []

    class SlowClient:
        entered = None

        async def generate(self, *args, **kwargs):
            if self.entered is not None:
                self.entered.set()
            try:
                await asyncio.sleep(10)
            finally:
                closed.append(True)

    real.ai.client = SlowClient()
    real.ai.settings = configuration(timeout_seconds=0.02)
    with pytest.raises(AppError) as exc:
        run(real.ai.answer(real.ctx, QuestionRequest(question="证据"), [real.source]))
    assert exc.value.code == "AI_TIMEOUT"
    # The overall budget may expire during authorization, before a model call exists to cancel.
    real.ai.settings = configuration()
    closed.clear()

    async def cancel_after_model_starts():
        real.ai.client.entered = asyncio.Event()
        task = asyncio.create_task(real.ai.answer(real.ctx, QuestionRequest(question="证据"), [real.source]))
        try:
            await asyncio.wait_for(real.ai.client.entered.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    run(cancel_after_model_starts())
    assert closed == [True]


def test_local_inference_process_is_reaped_on_timeout(real, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.ai.local_embeddings import LocalEmbeddings

    class Catalog:
        @staticmethod
        def list_supported_models():
            return [{"model": "local-fixture", "dim": 3, "model_file": "model.onnx"}]

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=Catalog))
    for name in ("model.onnx", "tokenizer.json", "config.json"):
        (tmp_path / name).write_text("isolated fixture")
    settings = configuration(
        embedding_backend="fastembed",
        embedding_model="local-fixture",
        local_model_dir=tmp_path,
        chunk_strategy="character-v1",
    )
    embedding = LocalEmbeddings(settings)

    class Process:
        returncode = None
        killed = False
        waited = False

        async def communicate(self, payload):
            await asyncio.sleep(10)

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True
            self.returncode = -9

    process = Process()

    async def spawn(*args, **kwargs):
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    real.ai.embedder = embedding
    real.ai.settings = configuration(timeout_seconds=0.02)
    with pytest.raises(AppError) as exc:
        run(real.ai.search(real.ctx, real.query))
    assert exc.value.code == "AI_TIMEOUT" and process.killed and process.waited


@pytest.mark.parametrize(
    "changes",
    [
        {"embedding_base_url": "https://other.invalid/v1"},
        {"embedding_api_key": "other-fixture-key"},
        {"base_url": "http://external.invalid/v1"},
        {"base_url": "https://user:password@external.invalid/v1"},
        {"embedding_backend": "unknown"},
        {"embedding_backend": "fastembed"},
    ],
)
def test_invalid_provider_configuration_is_rejected(changes):
    with pytest.raises(ValueError):
        configuration(**changes)


def test_explicit_remote_embedding_service_has_separate_credentials(real):
    settings = configuration(
        embedding_base_url="https://embedding.invalid/v1", embedding_api_key="embedding-only-key"
    )
    ai = RealAI(real.data, settings, reader=real.ai.reader)
    assert ai.embedder.settings.base_url == "https://embedding.invalid/v1"
    assert ai.embedder.settings.api_key == "embedding-only-key"
    assert ai.client.settings.api_key == "fixture-secret-not-a-key"


def test_model_insufficiency_and_bad_evidence_status_are_structured(real):
    def empty(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"insufficient_evidence": True, "statements": []})},
                    }
                ]
            },
        )

    real.ai.client = model_client(handler=empty)
    result = run(real.ai.answer(real.ctx, QuestionRequest(question="unknown"), [real.source]))
    assert result.insufficient_evidence and not result.sources and not result.citation_ids

    def invalid(request):
        field = {"text": "unsupported", "status": "not_mentioned", "citation_ids": []}
        fields = {
            name: field for name in ("research_question", "method", "results", "conclusion", "limitations")
        }
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(fields)}}]}
        )

    real.ai.client = model_client(handler=invalid)
    with pytest.raises(AppError) as exc:
        run(real.ai.extract(real.ctx, real.file.paper_id, [real.source]))
    assert exc.value.code == "AI_INVALID_RESPONSE"


def test_factory_config_and_explicit_composition(real, monkeypatch, tmp_path):
    monkeypatch.delenv("PAPERPILOT_AI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="configuration"):
        build_ai(real.data)
    with pytest.raises(RuntimeError, match="explicit"):
        create_ai_app(Settings(environment="test"), ai_settings=configuration())
    app = create_ai_app(
        Settings(environment="test", c_mode="real", storage_dir=tmp_path), ai_settings=configuration()
    )
    assert isinstance(app.state.adapters.ai, RealAI)
    assert "fixture-secret" not in repr(configuration())
    for name, value in {
        "BASE_URL": "https://model.invalid/v1",
        "API_KEY": "local-test-key",
        "CHAT_MODEL": "test-chat",
        "EMBEDDING_MODEL": "test-embedding",
        "EMBEDDING_DIMENSIONS": "3",
    }.items():
        monkeypatch.setenv("PAPERPILOT_AI_" + name, value)
    assert isinstance(build_ai(real.data), RealAI)
    monkeypatch.delenv("PAPERPILOT_AI_EMBEDDING_REVISION", raising=False)
    assert AISettings.from_env().embedding_dimensions == 3
    assert AISettings.from_env().embedding_revision is None
    monkeypatch.setenv("PAPERPILOT_AI_EMBEDDING_REVISION", "fixture-v1")
    assert AISettings.from_env().embedding_revision == "fixture-v1"
    monkeypatch.setenv("PAPERPILOT_AI_EMBEDDING_REVISION", " ")
    with pytest.raises(RuntimeError, match="configuration"):
        AISettings.from_env()


def test_business_upload_search_answer_evidence_draft_and_save(tmp_path):
    from conftest import finished

    settings = configuration()
    app = create_app(
        Settings(environment="test", c_mode="real", storage_dir=tmp_path),
        ai_factory=lambda data: RealAI(
            data, settings, reader=LocalPDFReader(tmp_path), client=model_client(settings)
        ),
    )
    with TestClient(app, headers={"X-PaperPilot-Client": "web"}) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        project = client.post("/api/v1/projects", json={"name": "C integration"}).json()["project_id"]
        base = f"/api/v1/projects/{project}"
        upload = client.post(
            base + "/papers",
            data={"title": "Evidence retrieval", "year": "2025"},
            files={
                "file": (
                    "paper.pdf",
                    pdf_bytes(["Evidence retrieval checks provenance.", "Limitations: an original fixture."]),
                    "application/pdf",
                )
            },
        )
        assert upload.status_code == 202
        result = finished(client, project, upload.json()["task"]["task_id"])
        assert result["status"] == "succeeded" and result["result"]["mock"] is False
        paper = upload.json()["paper"]["paper_id"]
        search = client.post(base + "/search", json={"query": "evidence", "mode": "keyword"})
        assert search.status_code == 200 and search.json()["mock"] is False
        assert all(not h["source"]["synthetic"] for h in search.json()["items"])
        outputs = {}
        for path in ("/answers", f"/papers/{paper}/evidence", "/report-drafts"):
            response = client.post(
                base + path,
                json={"question": "evidence", "paper_ids": [paper]}
                if path != f"/papers/{paper}/evidence"
                else None,
            )
            assert response.status_code == 202
            task = finished(client, project, response.json()["task_id"])
            assert task["status"] == "succeeded", task
            assert task["result"]["mock"] is False
            outputs[task["kind"]] = task["result"]
        assert outputs["answer"]["citation_ids"]
        assert outputs["evidence"]["method"]["status"] == "pending_verification"
        assert client.get(base + "/reports").json()["items"] == []
        saved = client.post(
            base + "/reports",
            json={
                "title": "Reviewed draft",
                "research_question": "evidence",
                "body": outputs["draft"]["body"],
                "sources": outputs["draft"]["sources"],
            },
        )
        assert saved.status_code == 201 and saved.json()["number"] == 1
        assert client.get(base + f"/papers/{paper}/evidence").json()["items"]
