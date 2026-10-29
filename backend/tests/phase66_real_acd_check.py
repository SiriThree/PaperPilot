"""Opt-in A+C+policy D integration; at most two external chat requests.

Run only in an explicitly created disposable PostgreSQL environment. The PDF and
account are fictional test fixtures. D execution remains disabled.
"""

import os
import secrets
import time

import pytest
from fastapi.testclient import TestClient

from ai_contracts.support import pdf_bytes
from app.adapters.data_binding import DataBinding
from app.ai.adapter import RealAI
from app.ai.client import ModelClient
from app.ai.files import LocalPDFReader
from app.ai.models import Extraction, Narrative
from app.ai.settings import AISettings
from app.config import Settings
from app.data.authentication import PostgresSessions
from app.main import create_app
from app.security import build_security
from test_outbox_delivery import harness  # noqa: F401


def test_isolated_real_acd_with_credential_session(harness, tmp_path):  # noqa: F811
    if os.getenv("PAPERPILOT_REAL_ACD_OPT_IN") != "1" or os.getenv("PAPERPILOT_PHASE5A_ISOLATED") != "1":
        pytest.fail("Explicit isolated database and live model opt-in required")
    configuration = AISettings.from_env()
    if configuration.embedding_backend != "fastembed":
        pytest.fail("This test requires the prepared local embedding backend")
    scenario = os.getenv("PAPERPILOT_ACD_CASE", "sparse")
    if scenario not in {"sparse", "structured"}:
        pytest.fail("Unknown fictional evaluation case")

    calls = {"answer": 0, "evidence": 0}

    class LimitedModel(ModelClient):
        async def generate(self, shape, *, instruction, payload):
            kind = {Narrative: "answer", Extraction: "evidence"}.get(shape)
            if kind is None or calls[kind] >= 1:
                raise RuntimeError("External model call budget exceeded")
            calls[kind] += 1
            return await super().generate(shape, instruction=instruction, payload=payload)

    password = secrets.token_urlsafe(24)
    PostgresSessions(harness.database).initialize_password("alice", password)
    settings = Settings(
        environment="test",
        a_mode="persistent",
        c_mode="real",
        d_mode="real",
        auth_mode="credentials",
        storage_dir=tmp_path,
        allowed_origins=("http://trusted.test",),
    )

    def app():
        return create_app(
            settings,
            data_binding=DataBinding(harness.data, harness.database.ready),
            ai_factory=lambda data: RealAI(
                data, configuration, reader=LocalPDFReader(tmp_path), client=LimitedModel(configuration)
            ),
            security_factory=build_security,
        )

    def wait_task(client, base, task):
        for _ in range(1200):
            result = client.get(f"{base}/tasks/{task}").json()
            if result["status"] in ("succeeded", "failed"):
                assert result["status"] == "succeeded", "Background task failed"
                return result["result"]
            time.sleep(0.1)
        pytest.fail("Background task did not complete")

    def authenticate(client):
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        headers = {"Origin": "http://trusted.test", "X-CSRF-Token": csrf}
        result = client.post(
            "/api/v1/auth/login", headers=headers, json={"username": "alice", "password": password}
        )
        assert result.status_code == 200
        return headers

    def fictional_marked(text):
        return any(
            marker in text.lower() for marker in ("fictional", "simulat", "hypothetical", "虚构", "模拟")
        )

    try:
        with TestClient(app(), base_url="http://trusted.test") as client:
            headers = authenticate(client)
            project = client.post(
                "/api/v1/projects", headers=headers, json={"name": "Fictional study"}
            ).json()["project_id"]
            base = f"/api/v1/projects/{project}"
            if scenario == "sparse":
                fictional_text = (
                    "FICTIONAL EXAMPLE ONLY. The simulated study compares keyword and vector retrieval. "
                    "The simulated dataset contains twelve documents. This is not real scientific evidence."
                )
            else:
                fictional_text = (
                    "FICTIONAL EXAMPLE ONLY. Research question: can vector retrieval find more relevant "
                    "fictional documents than keyword retrieval? Method: a simulated evaluation compares "
                    "both methods on twelve fictional documents with three manually marked relevant items. "
                    "Results: keyword retrieval found two relevant items, while vector retrieval found three. "
                    "Conclusion: vector retrieval found one more relevant item in this fictional example. "
                    "Limitations: the example is tiny and invented; it is not real scientific evidence."
                )
            raw_pdf = pdf_bytes([fictional_text])
            upload = client.post(
                base + "/papers",
                headers={**headers, "Idempotency-Key": "isolated-upload"},
                files={"file": ("fictional.pdf", raw_pdf, "application/pdf")},
                data={"title": "Fictional"},
            )
            assert upload.status_code == 202
            paper = upload.json()["paper"]["paper_id"]
            wait_task(client, base, upload.json()["task"]["task_id"])
            stored = client.get(base + f"/papers/{paper}").json()["sources"]
            assert stored and all(s["paper_id"] == paper and s["page_number"] == 1 for s in stored)
            for mode in ("keyword", "semantic", "hybrid"):
                found = client.post(
                    base + "/search",
                    headers=headers,
                    json={"query": "simulated dataset twelve documents", "mode": mode, "limit": 3},
                )
                assert found.status_code == 200 and found.json()["items"]
                assert all(item["paper"]["paper_id"] == paper for item in found.json()["items"])
            query = client.post(
                base + "/answers",
                headers={**headers, "Idempotency-Key": "isolated-answer"},
                json={
                    "question": "How many documents are in the fictional simulated dataset?",
                    "paper_ids": [paper],
                },
            )
            assert query.status_code == 202
            answer = wait_task(client, base, query.json()["task_id"])
            assert not answer["insufficient_evidence"] and answer["sources"]
            assert "12" in answer["answer"] or "twelve" in answer["answer"].lower()
            assert fictional_marked(answer["answer"]), "Answer lost its fictional qualification"
            evidence = client.post(
                base + f"/papers/{paper}/evidence",
                headers={**headers, "Idempotency-Key": "isolated-evidence"},
            )
            assert evidence.status_code == 202
            evidence_result = wait_task(client, base, evidence.json()["task_id"])
            print(
                "Fictional case:",
                scenario,
                "evidence statuses:",
                {
                    key: {
                        "status": evidence_result[key]["status"],
                        "source_count": len(evidence_result[key]["sources"]),
                        "fictional_marked": fictional_marked(evidence_result[key]["text"] or ""),
                    }
                    for key in ("research_question", "method", "results", "conclusion", "limitations")
                },
            )
            evidence_sources = [
                source
                for key in ("research_question", "method", "results", "conclusion", "limitations")
                for source in evidence_result[key]["sources"]
            ]
            for key in ("research_question", "method", "results", "conclusion", "limitations"):
                field = evidence_result[key]
                if field["status"] == "pending_verification":
                    assert fictional_marked(field["text"] or ""), "Evidence lost its fictional qualification"
            actual = {(s["paper_id"], s["chunk_id"], s["page_number"], s["excerpt"]) for s in stored}
            for source in answer["sources"] + evidence_sources:
                assert (
                    source["paper_id"],
                    source["chunk_id"],
                    source["page_number"],
                    source["excerpt"],
                ) in actual
            saved = client.post(
                base + "/reports",
                headers=headers,
                json={
                    "title": "Fictional",
                    "research_question": "Sample size",
                    "body": "Reviewed [1]",
                    "sources": answer["sources"],
                },
            )
            assert saved.status_code == 201
            report = saved.json()["report_id"]
        with TestClient(app(), base_url="http://trusted.test") as client:
            authenticate(client)
            assert client.get(base + f"/papers/{paper}").json()["sources"] == stored
            history = client.get(base + f"/reports/{report}/versions").json()["items"]
            assert history and history[0]["sources"] == answer["sources"]
        assert calls == {"answer": 1, "evidence": 1}
        # A model may decline all fields for this fictional example. Treat that
        # as a quality finding after completing independent persistence checks.
        assert evidence_sources, "Model supplied no cited evidence for the fictional document"
    finally:
        print(f"Live chat calls: answer={calls['answer']}, evidence={calls['evidence']}; retries=0")
