"""A+D then A+C+D over real PostgreSQL; C's model HTTP endpoint is a local test double."""

from contextlib import contextmanager, nullcontext
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import SecurityAdapter, build_security
from security_contracts.postgres_provider import postgres_workspace

C_COMMIT = "1f430936acf23c598a91ce794d70ca8c536d39e4"
pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        os.getenv("PAPERPILOT_SECURITY_TEAM_TESTS") != "1",
        reason="Team integration requires explicit opt-in, Docker and pinned A/C checkouts",
    ),
]


@pytest.fixture(scope="module")
def pg(record_testsuite_property):
    with postgres_workspace(record_testsuite_property) as workspace:
        yield workspace


@contextmanager
def c_reference():
    import app

    current = os.getenv("PAPERPILOT_SECURITY_CURRENT_SOURCE") == "1"
    source = (
        Path(__file__).resolve().parents[2]
        if current
        else Path(os.environ["PAPERPILOT_SECURITY_TEST_C_SOURCE"]).resolve()
    )
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if not current:
        assert head == C_COMMIT, "C reference must match the documented commit"
        subprocess.run(
            ["git", "-C", str(source), "diff", "--exit-code", "HEAD", "--", "backend/app/ai"], check=True
        )
    old_paths = list(app.__path__)
    old_modules = {
        key: value for key, value in sys.modules.items() if key == "app.ai" or key.startswith("app.ai.")
    }
    old_ai = getattr(app, "ai", None)
    app.__path__ = [str(source / "backend/app"), *old_paths]
    for key in old_modules:
        del sys.modules[key]
    if hasattr(app, "ai"):
        delattr(app, "ai")
    try:
        yield
    finally:
        app.__path__ = old_paths
        for name in list(sys.modules):
            if name == "app.ai" or name.startswith("app.ai."):
                del sys.modules[name]
        sys.modules.update(old_modules)
        if old_ai is None:
            if hasattr(app, "ai"):
                delattr(app, "ai")
        else:
            app.ai = old_ai


@contextmanager
def local_model():
    state = SimpleNamespace(calls=[], unavailable=False)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.calls.append(self.path)
            if state.unavailable:
                self.send_response(503)
                self.end_headers()
                return
            if self.path.endswith("/embeddings"):
                document = {
                    "data": [
                        {"index": index, "embedding": [1.0, 0.0, 0.0]}
                        for index, _ in enumerate(payload["input"])
                    ]
                }
            else:
                supplied = json.loads(payload["messages"][-1]["content"])["sources"]
                citation = supplied[0]["citation_id"]
                if payload["response_format"]["json_schema"]["name"] == "Extraction":
                    content = {
                        name: {
                            "status": "pending_verification",
                            "text": "Controlled integration evidence",
                            "citation_ids": [citation],
                        }
                        for name in ("research_question", "method", "results", "conclusion", "limitations")
                    }
                else:
                    content = {
                        "insufficient_evidence": False,
                        "statements": [
                            {"text": "Controlled integration statement", "citation_ids": [citation]}
                        ],
                    }
                document = {
                    "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]
                }
            body = json.dumps(document).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def pdf_bytes(label):
    # A tiny valid text PDF exercises C's actual parser subprocess without a document dependency.
    stream = f"BT /F1 12 Tf 40 160 Td (PaperPilot RAG integration {label}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    result, offsets = bytearray(b"%PDF-1.4\n"), [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    position = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{position}\n%%EOF\n".encode())
    return bytes(result)


def done(client, base, task):
    assert task.status_code == 202, task.text
    task_id = task.json().get("task_id") or task.json()["task"]["task_id"]
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(base + f"/tasks/{task_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in ("succeeded", "failed"):
            assert body["status"] == "succeeded", body
            return body["result"]
        time.sleep(0.03)
    pytest.fail("Real composition task did not finish")


@pytest.mark.parametrize("c_mode", ["mock", "real"])
def test_a_d_then_a_c_d_http_workflow(pg, tmp_path, c_mode, record_testsuite_property):
    from app.data.binding import compose_data_binding
    from app.data.settings import DataSettings

    record_testsuite_property(
        "security_c_reference",
        ("current_checkout" if os.getenv("PAPERPILOT_SECURITY_CURRENT_SOURCE") == "1" else C_COMMIT)
        if c_mode == "real"
        else "Mock",
    )
    record_testsuite_property("security_model_transport", "local deterministic HTTP double; no live model")
    settings = Settings(
        environment="test",
        a_mode="persistent",
        c_mode=c_mode,
        d_mode="real",
        storage_dir=tmp_path / "uploads",
    )
    binding = compose_data_binding(pg.config, DataSettings())
    factory = partial(build_security, readers=pg.readers)
    with c_reference() if c_mode == "real" else nullcontext(), local_model() as model:
        if c_mode == "real":
            from app.ai.bootstrap import create_app as compose_real_c
            from app.ai.settings import AISettings

            application = compose_real_c(
                settings,
                ai_settings=AISettings(
                    base_url=model.url,
                    api_key="local-test-key",
                    chat_model="test-chat",
                    embedding_model="test-embedding",
                    embedding_dimensions=3,
                    embedding_revision="test-v1",
                ),
                data_binding=binding,
                security_factory=factory,
            )
            assert application.state.adapters.ai.__class__.__name__ == "RealAI"
        else:
            application = create_app(settings, data_binding=binding, security_factory=factory)
        assert type(application.state.adapters.security) is SecurityAdapter
        assert application.state.adapters.data.__class__.__name__ == "PostgresData"
        base = f"/api/v1/projects/{pg.contexts[0].project_id}"
        with TestClient(application, headers={"X-PaperPilot-Client": "web"}) as client:
            assert (
                client.post(base + "/secure-queries", json={"question": "按年份统计论文数量"}).status_code
                == 401
            )
            assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
            upload = client.post(
                base + "/papers",
                files={"file": ("integration.pdf", pdf_bytes(c_mode), "application/pdf")},
                data={"title": f"RAG {c_mode} integration", "year": "2026" if c_mode == "real" else "2025"},
            )
            process = done(client, base, upload)
            paper_id = upload.json()["paper"]["paper_id"]
            assert process["mock"] is (c_mode == "mock")
            if c_mode == "real":
                assert process["sources"] and all(not source["synthetic"] for source in process["sources"])
            search = client.post(base + "/search", json={"query": "RAG", "mode": "keyword"})
            assert search.status_code == 200 and search.json()["mock"] is (c_mode == "mock")
            assert any(hit["paper"]["paper_id"] == paper_id for hit in search.json()["items"])

            def query(question):
                request_id = str(uuid4())
                response = client.post(
                    base + "/secure-queries",
                    json={"question": question},
                    headers={"X-Request-ID": request_id},
                )
                assert response.status_code == 200, response.text
                result = response.json()
                assert (result["decision"], result["execution_status"], result["mock"]) == (
                    "allowed",
                    "executed",
                    False,
                )
                assert result["request_id"] == request_id and len(response.content) <= 256 * 1024
                audit = client.get(
                    base + "/audit-events", params={"action": "security.query.finished", "limit": 100}
                )
                event = next(item for item in audit.json()["items"] if item["request_id"] == request_id)
                assert event["execution_status"] == "executed" and not event["mock"]
                return result

            assert any(row["paper_id"] == paper_id for row in query("查询尚未完成证据提取的论文")["rows"])
            evidence = done(client, base, client.post(base + f"/papers/{paper_id}/evidence"))
            assert evidence["mock"] is (c_mode == "mock")
            assert not any(row["paper_id"] == paper_id for row in query("查询尚未完成证据提取的论文")["rows"])
            assert any(
                row["year"] == (2026 if c_mode == "real" else 2025)
                for row in query("按年份统计论文数量")["rows"]
            )
            query("按研究主题统计论文数量")
            question = {"question": "RAG", "paper_ids": [paper_id]}
            answer = done(client, base, client.post(base + "/answers", json=question))
            draft = done(client, base, client.post(base + "/report-drafts", json=question))
            assert answer["mock"] is (c_mode == "mock") and draft["mock"] is (c_mode == "mock")
            report = client.post(
                base + "/reports",
                json={
                    "title": "Integration report",
                    "research_question": "RAG",
                    "body": draft["body"],
                    "sources": draft["sources"],
                },
            )
            assert report.status_code == 201, report.text
            assert client.get(base + "/reports").status_code == 200
            if c_mode == "real":
                assert "/v1/embeddings" in model.calls and "/v1/chat/completions" in model.calls
                model.unavailable = True
                unavailable = client.post(
                    base + "/search", json={"query": "RAG", "mode": "hybrid", "year_from": 2026}
                )
                assert unavailable.status_code == 503 and unavailable.json()["code"] == "AI_UNAVAILABLE"
                query("按年份统计论文数量")
            else:
                assert not model.calls
            rejected = client.post(base + "/secure-queries", json={"question": "SELECT * FROM users"})
            assert rejected.status_code == 200 and rejected.json()["execution_status"] == "not_executed"
            assert client.post("/api/v1/auth/login", json={"username": "eve"}).status_code == 200
            denied = client.post(base + "/secure-queries", json={"question": "按年份统计论文数量"})
            assert denied.status_code == 404 and "rows" not in denied.json()
        assert binding.data.database._closed, "Composition must close A's owned connection pool"
