"""Corpus tooling checks use tiny protocol fixtures, never demo research evidence."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.schemas import SearchRequest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def scripts(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    modules = []
    for name in ["prepare_arxiv_corpus", "ingest_research_corpus"]:
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


def test_metadata_filters_domain_and_withdrawn_items(scripts):
    prepare, _ = scripts
    entries = [
        ("2401.00001v2", "Retrieval-Augmented Generation for NLP", "cs.CL", ""),
        ("2401.00002v1", "Retrieval Augmented Generation", "cs.CV", ""),
        ("2401.00003v1", "Retrieval Augmented Generation", "cs.CL", "This paper is withdrawn"),
        ("2401.00004v1", "An unrelated language model", "cs.CL", ""),
    ]
    body = '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:ar="http://arxiv.org/schemas/atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/"><o:totalResults>4</o:totalResults>'
    for aid, title, category, comment in entries:
        body += f"""<entry><id>http://arxiv.org/abs/{aid}</id><title>{title}</title>
        <published>2024-01-01T00:00:00Z</published><category term="{category}"/>
        <author><name>Fixture Author</name></author><ar:comment>{comment}</ar:comment></entry>"""
    papers, total = prepare.parse_feed((body + "</feed>").encode())
    assert total == 4 and len(papers) == 1
    assert papers[0]["arxiv_id"] == "2401.00001" and papers[0]["version"] == 2
    assert papers[0]["pdf_url"] == "https://arxiv.org/pdf/2401.00001v2"
    with pytest.raises(ValueError, match="versioned"):
        prepare.parse_feed((body + "</feed>").replace("2401.00001v2", "2401.00001").encode())


def import_fixture(tmp_path, count=1):
    payload = b"protocol test fixture, not a research PDF"
    papers = [
        {
            "key": f"p{i}",
            "filename": f"p{i}.pdf",
            "pdf_url": "https://arxiv.org/pdf/2401.00001v1",
            "title": f"Fixture {i}",
            "year": 2024,
            "arxiv_id": f"2401.{i:05}",
            "chunk_count": 1,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for i in range(count)
    ]
    catalog = {
        "version": "test-v1",
        "query": "fixture",
        "target_count": count,
        "status": "complete",
        "papers": papers,
        "projects": [
            {
                "key": "test",
                "name": "Test",
                "description": "Test",
                "paper_keys": [p["key"] for p in papers],
                "questions": [],
            }
        ],
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    for p in papers:
        (tmp_path / p["filename"]).write_bytes(payload)
    password = tmp_path / "password"
    password.write_text("test-only")
    return SimpleNamespace(
        catalog=path,
        expected_count=count,
        api_base="http://localhost:18802",
        state=tmp_path / "state.json",
        password_file=password,
        username="alice",
        origin="http://localhost:15175",
        pdf_dir=tmp_path,
        max_pending=3,
        wait_seconds=10,
        watch=False,
    )


def mock_client(monkeypatch, ingest, handler):
    original_client = httpx.Client
    monkeypatch.setattr(
        ingest.httpx, "Client", lambda **kw: original_client(transport=httpx.MockTransport(handler), **kw)
    )
    monkeypatch.setattr(ingest, "authenticate", lambda *args: {"user_id": "owner"})
    monkeypatch.setattr(ingest.time, "sleep", lambda *args: None)


def project_response():
    return httpx.Response(
        200,
        json={
            "items": [
                {
                    "owner_id": "owner",
                    "project_id": "project",
                    "description": "[paperpilot-real-corpus:test-v1:test]",
                }
            ]
        },
    )


def test_lost_upload_ack_reuses_key_and_verifies_already_completed_task(tmp_path, monkeypatch, scripts):
    _, ingest = scripts
    args = import_fixture(tmp_path)
    keys, details = [], []

    def handler(request):
        if request.url.path == "/api/v1/projects":
            return project_response()
        if request.method == "POST":
            assert request.url.path.endswith("/papers")
            keys.append(request.headers["Idempotency-Key"])
            if len(keys) == 1:
                raise httpx.ReadError("simulated lost acknowledgement", request=request)
            return httpx.Response(
                202, json={"paper": {"paper_id": "paper"}, "task": {"task_id": "task", "status": "succeeded"}}
            )
        if request.url.path.endswith("/tasks/task"):
            return httpx.Response(200, json={"status": "succeeded", "error": None})
        details.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "paper": {"status": "searchable"},
                "sources": [{"paper_id": "paper", "chunk_id": "chunk", "page_number": 1, "synthetic": False}],
            },
        )

    mock_client(monkeypatch, ingest, handler)
    with pytest.raises(httpx.ReadError):
        ingest.import_corpus(args)
    saved = json.loads(args.state.read_text())["projects"]["test"]["papers"]["p0"]
    assert saved["upload_key"] == keys[0] and "task_id" not in saved
    completed = ingest.import_corpus(args)
    assert len(keys) == 2 and keys[0] == keys[1] and len(details) == 1
    assert completed["projects"]["test"]["papers"]["p0"]["source_count"] == 1
    ingest.import_corpus(args)
    assert len(keys) == 2  # A completed rerun neither reuploads nor duplicates tasks.


def test_queue_bounds_pending_uploads(tmp_path, monkeypatch, scripts):
    _, ingest = scripts
    args = import_fixture(tmp_path, count=6)
    uploads = []

    def handler(request):
        if request.url.path == "/api/v1/projects":
            return project_response()
        uploads.append(request.headers["Idempotency-Key"])
        return httpx.Response(
            202,
            json={
                "paper": {"paper_id": f"p{len(uploads)}"},
                "task": {"task_id": f"t{len(uploads)}", "status": "pending"},
            },
        )

    mock_client(monkeypatch, ingest, handler)

    def stop_after_iteration(*args):
        raise InterruptedError("test stops before polling")

    monkeypatch.setattr(ingest.time, "sleep", stop_after_iteration)
    with pytest.raises(InterruptedError):
        ingest.import_corpus(args)
    assert len(uploads) == 3 and len(set(uploads)) == 3


def test_large_paper_drains_queue_then_runs_alone(tmp_path, monkeypatch, scripts):
    _, ingest = scripts
    args = import_fixture(tmp_path, count=6)
    args.max_pending = 8
    args.serialize_above_chunks = 2
    catalog = json.loads(args.catalog.read_text())
    catalog["papers"][1]["chunk_count"] = 2
    args.catalog.write_text(json.dumps(catalog))
    outstanding, uploads = {}, []

    def handler(request):
        if request.url.path == "/api/v1/projects":
            return project_response()
        if request.method == "POST":
            key = request.headers["Idempotency-Key"].rsplit("-", 1)[-1]
            if key == "p1":
                assert not outstanding
            else:
                assert "p1" not in outstanding
            outstanding[key] = True
            uploads.append(key)
            return httpx.Response(
                202, json={"paper": {"paper_id": key}, "task": {"task_id": key, "status": "pending"}}
            )
        key = request.url.path.rsplit("/", 1)[-1]
        if "/tasks/" in request.url.path:
            outstanding.pop(key)
            return httpx.Response(200, json={"status": "succeeded", "error": None})
        return httpx.Response(
            200,
            json={
                "paper": {"status": "searchable"},
                "sources": [
                    {"paper_id": key, "chunk_id": f"{key}-{i}", "page_number": 1, "synthetic": False}
                    for i in range(2 if key == "p1" else 1)
                ],
            },
        )

    mock_client(monkeypatch, ingest, handler)
    result = ingest.import_corpus(args)
    assert result["status"] == "complete" and uploads == ["p0", "p1", "p2", "p3", "p4", "p5"]


@pytest.mark.parametrize("mutation", ["synthetic", "wrong_paper", "missing_chunk", "duplicate_chunk"])
def test_import_requires_real_complete_sources(scripts, mutation):
    _, ingest = scripts
    source = {"chunk_id": "chunk", "paper_id": "paper", "page_number": 1, "synthetic": False}
    detail = {"paper": {"status": "searchable"}, "sources": [source]}
    paper = {"chunk_count": 1}
    if mutation == "synthetic":
        source["synthetic"] = True
    elif mutation == "wrong_paper":
        source["paper_id"] = "other"
    elif mutation == "missing_chunk":
        paper["chunk_count"] = 2
    else:
        detail["sources"].append(copy.deepcopy(source))
        paper["chunk_count"] = 2
    with pytest.raises(ValueError):
        ingest.verify_detail(detail, paper, {"paper_id": "paper"})


def test_frozen_300_paper_catalog_snapshot_and_workload_agree():
    directory = ROOT / "demo/rag-300"
    payload = (directory / "catalog.json").read_bytes()
    catalog = json.loads(payload)
    papers = catalog["papers"]
    summary = json.loads((directory / "summary.json").read_text())
    subsets = json.loads((directory / "subsets.json").read_text())
    workload = json.loads((directory / "workload.json").read_text())
    assert catalog["status"] == "complete" and len(papers) == catalog["target_count"] == 300
    assert len({p["arxiv_id"] for p in papers}) == len({p["sha256"] for p in papers}) == 300
    assert all("cs.CL" in p["categories"] and p["pages"] >= 3 and p["bytes"] >= 10000 for p in papers)
    assert summary["paper_count"] == summary["process_indexes"] == 300
    assert summary["chunks"] == summary["embeddings"] == sum(p["chunk_count"] for p in papers)
    assert summary["synthetic_chunks"] == 0
    assert summary["catalog_sha256"] == subsets["catalog_sha256"] == hashlib.sha256(payload).hexdigest()
    for subset in subsets["subsets"]:
        selected = papers[: subset["paper_count"]]
        assert subset["paper_keys"] == [p["key"] for p in selected]
        assert subset["chunk_count"] == sum(p["chunk_count"] for p in selected)
    assert len(workload["queries"]) == len({q["id"] for q in workload["queries"]}) == 40
    assert workload["relevance_labels"] is None
    for query in workload["queries"]:
        SearchRequest.model_validate({k: v for k, v in query.items() if k != "id"})
