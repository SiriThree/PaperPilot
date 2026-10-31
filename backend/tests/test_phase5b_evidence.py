"""A PostgreSQL + C RealAI with a controlled model; never a real-model quality test."""

import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from ai_contracts.support import configuration, model_client, model_response, pdf_bytes
from app.adapters.ports import Context
from app.ai.adapter import RealAI
from app.ai.files import LocalPDFReader
from app.config import Settings
from app.data.binding import compose_data_binding
from app.data.settings import DataSettings
from app.main import create_app
from test_phase5a_persistent import WEB, database as isolated_database, wait_task  # noqa: F401

pytestmark = pytest.mark.real_database


def test_target_sources_survive_global_top_50_and_report_snapshot(isolated_database, monkeypatch):  # noqa: F811
    config, storage = isolated_database
    calls = []

    def model(request):
        if request.url.path.endswith("/chat/completions"):
            calls.append(json.loads(json.loads(request.content)["messages"][1]["content"]))
        return model_response(request)

    ai_settings = configuration(embedding_revision="phase5b-fixture-v1")
    app = create_app(
        Settings(environment="test", a_mode="persistent", c_mode="real", storage_dir=storage),
        data_binding=compose_data_binding(config, DataSettings()),
        ai_factory=lambda data: RealAI(
            data, ai_settings, reader=LocalPDFReader(storage), client=model_client(ai_settings, model)
        ),
    )
    with TestClient(app, headers=WEB) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        project = client.post("/api/v1/projects", json={"name": "A+C evidence"}).json()["project_id"]
        base = f"/api/v1/projects/{project}"
        response = client.post(
            base + "/papers",
            files={"file": ("target.pdf", pdf_bytes(["Target evidence in this paper."]), "application/pdf")},
            data={"title": "Target"},
            headers={"Idempotency-Key": "phase5b-upload"},
        )
        assert response.status_code == 202
        paper, task = response.json()["paper"]["paper_id"], response.json()["task"]["task_id"]
        assert wait_task(client, project, task)["status"] == "succeeded"

        competing = client.post(
            base + "/papers",
            files={
                "file": (
                    "other.pdf",
                    pdf_bytes([f"Competing source {i}." for i in range(50)]),
                    "application/pdf",
                )
            },
            data={"title": "Competing"},
            headers={"Idempotency-Key": "phase5b-competing"},
        ).json()
        other_paper = competing["paper"]["paper_id"]
        assert wait_task(client, project, competing["task"]["task_id"])["status"] == "succeeded"
        competing_sources = client.get(base + f"/papers/{other_paper}").json()["sources"]
        assert len(competing_sources) == 50
        top50 = SimpleNamespace(
            items=[
                SimpleNamespace(paper=SimpleNamespace(paper_id=other_paper), source=s)
                for s in competing_sources
            ]
        )
        search_calls = []

        # The former evidence path used a global search; a competing top-50 would
        # omit this paper. Failing on any search ensures extraction never uses it.
        async def competing_search(*args):
            search_calls.append(1)
            return top50

        monkeypatch.setattr(app.state.service, "search", competing_search)
        result = client.post(
            base + f"/papers/{paper}/evidence", headers={"Idempotency-Key": "phase5b-evidence"}
        )
        assert result.status_code == 202
        task_id = result.json()["task_id"]
        replay = client.post(
            base + f"/papers/{paper}/evidence", headers={"Idempotency-Key": "phase5b-evidence"}
        )
        assert replay.json()["task_id"] == task_id
        finished = wait_task(client, project, task_id)
        assert finished["status"] == "succeeded", finished
        assert not search_calls
        source = finished["result"]["method"]["sources"][0]
        assert source["paper_id"] == paper and source["citation_id"] == "1"
        assert calls and calls[-1]["sources"][0]["chunk_id"] == source["chunk_id"]
        assert len(calls[-1]["sources"]) <= 50
        assert sum(len(s["excerpt"]) for s in calls[-1]["sources"]) <= 60000
        saved = client.post(
            base + "/reports",
            json={
                "title": "Evidence snapshot",
                "research_question": "Target",
                "body": "Reviewed [1]",
                "sources": [source],
            },
        )
        assert saved.status_code == 201
        history = client.get(base + f"/reports/{saved.json()['report_id']}/versions").json()["items"]
        assert history[0]["sources"] == [source]
        other = client.post("/api/v1/projects", json={"name": "Other"}).json()["project_id"]
        assert client.post(f"/api/v1/projects/{other}/papers/{paper}/evidence").status_code == 404
        member = client.post(base + "/members", json={"username": "bob"}).json()
        assert member["user"]["user_id"]
        data = app.state.adapters.data
        owner = data.user_by_name("alice")
        owner_ctx = Context(user_id=owner.user_id, project_id=UUID(project), request_id=uuid4())

        def revoke_during_model(request):
            data.remove_member(owner_ctx, UUID(member["user"]["user_id"]))
            return model_response(request)

        app.state.adapters.ai.client = model_client(ai_settings, revoke_during_model)
        assert client.post("/api/v1/auth/login", json={"username": "bob"}).status_code == 200
        revoked_task = client.post(base + f"/papers/{paper}/evidence").json()["task_id"]
        # Wait as owner: revoked users must no longer inspect even their own task.
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        outcome = wait_task(client, project, revoked_task)
        assert outcome["status"] == "failed" and outcome["result"] is None
        assert outcome["error"]["code"] == "RESOURCE_NOT_FOUND"
        assert client.get(base + f"/papers/{paper}/evidence").json()["items"]
        assert client.post("/api/v1/auth/login", json={"username": "bob"}).status_code == 200
        assert client.get(base + f"/tasks/{task_id}").status_code == 404
        assert client.post(base + f"/papers/{paper}/evidence").status_code == 404
