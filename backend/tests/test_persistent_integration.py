"""Business integration on member A's persistent PostgreSQL binding.

Runs B's real routes and worker against the real DataPort through HTTP; B's
tests/conftest.py and existing tests stay untouched (module fixtures here shadow
conftest only inside this file). Skips cleanly when the day0 PostgreSQL environment
is unavailable, keeping the default Mock baseline intact. Test order matters:
identity is switched on the shared client, and the restart test runs last.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.data.binding import compose_data_binding
from app.data.database import ConnectionConfig, PostgresDatabase
from app.data.provider import disposable_database
from app.data.settings import DataSettings
from app.main import create_app

pytestmark = pytest.mark.real_database
WEB = {"X-PaperPilot-Client": "web"}
PDF = b"%PDF-1.4\n persistent-integration synthetic payload"
TITLE = "业务联调合成论文"
YEAR = 2024


@dataclass
class Stack:
    """One disposable database plus independently created app/binding instances."""

    config: ConnectionConfig
    storage: Path
    bindings: list = field(default_factory=list)

    def new_app(self):
        database = PostgresDatabase(self.config)
        database.migrate()
        database.seed_development_users()
        binding = compose_data_binding(self.config, DataSettings.from_env())
        self.bindings.append(binding)
        settings = Settings(environment="test", a_mode="persistent", storage_dir=self.storage)
        return create_app(settings, data_binding=binding)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    manager = disposable_database("paperpilot_business_")
    try:
        config = manager.__enter__()
    except RuntimeError as exc:  # no test credentials configured
        pytest.skip(f"persistent business integration unavailable: {exc}")
    except Exception as exc:  # noqa: BLE001 - any connectivity failure just skips the module
        pytest.skip(f"PostgreSQL day0 environment unreachable: {type(exc).__name__}")
    instance = Stack(config=config, storage=tmp_path_factory.mktemp("persistent-business"))
    instance.app = instance.new_app()
    try:
        yield instance
    finally:
        for binding in instance.bindings:
            binding.close()
        manager.__exit__(None, None, None)


@pytest.fixture(scope="module")
def client(stack):
    with TestClient(stack.app, headers=WEB) as value:
        yield value


@pytest.fixture(scope="module")
def signed(client):
    response = client.post("/api/v1/auth/login", json={"username": "alice"})
    assert response.status_code == 200
    return client


@pytest.fixture(scope="module")
def project(signed):
    response = signed.post("/api/v1/projects", json={"name": "业务联调项目", "description": "persistent"})
    assert response.status_code == 201
    return response.json()["project_id"]


def wait_task(client, project_id, task_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/projects/{project_id}/tasks/{task_id}")
        assert response.status_code == 200, response.text
        task = response.json()
        if task["status"] in {"succeeded", "failed"}:
            return task
        time.sleep(0.05)
    raise AssertionError("task did not finish in time")


def upload(client, project_id, *, key, title=TITLE, year=YEAR, payload=PDF):
    return client.post(
        f"/api/v1/projects/{project_id}/papers",
        files={"file": ("integration.pdf", payload, "application/pdf")},
        data={"title": title, "year": str(year)},
        headers={"Idempotency-Key": key},
    )


def test_health_reports_registered_persistence(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["modes"]["a"] == "persistent"
    assert "registered_persistence_provider" in body["limitations"]


def test_upload_replay_processing_and_sources(signed, project):
    first = upload(signed, project, key="biz-upload-1")
    assert first.status_code == 202, first.text
    paper_id, task_id = first.json()["paper"]["paper_id"], first.json()["task"]["task_id"]
    assert first.json()["paper"]["status"] == "pending"

    replay = upload(signed, project, key="biz-upload-1")
    assert replay.status_code == 202
    assert replay.json()["paper"]["paper_id"] == paper_id
    assert replay.json()["task"]["task_id"] == task_id

    duplicate = upload(signed, project, key="biz-upload-other-key")
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "DUPLICATE_PAPER"

    task = wait_task(signed, project, task_id)
    assert task["status"] == "succeeded" and task["result"]["kind"] == "process"

    detail = signed.get(f"/api/v1/projects/{project}/papers/{paper_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["paper"]["status"] == "searchable" and body["paper"]["mock"] is False
    assert len(body["sources"]) == 1

    papers = signed.get(f"/api/v1/projects/{project}/papers", params={"year_from": 2024, "year_to": 2024})
    assert [p["paper_id"] for p in papers.json()["items"]] == [paper_id]
    assert signed.get(f"/api/v1/projects/{project}/papers", params={"year_from": 2025}).json()["items"] == []


def test_answer_and_evidence_complete_through_worker(signed, project):
    papers = signed.get(f"/api/v1/projects/{project}/papers").json()["items"]
    paper_id = papers[0]["paper_id"]

    answer = signed.post(
        f"/api/v1/projects/{project}/answers",
        json={"question": "业务联调问题", "paper_ids": [paper_id]},
        headers={"Idempotency-Key": "biz-answer-1"},
    )
    assert answer.status_code == 202
    task = wait_task(signed, project, answer.json()["task_id"])
    assert task["status"] == "succeeded" and task["result"]["kind"] == "answer"
    assert task["result"]["sources"][0]["paper_id"] == paper_id

    evidence = signed.post(f"/api/v1/projects/{project}/papers/{paper_id}/evidence")
    assert evidence.status_code == 202
    task = wait_task(signed, project, evidence.json()["task_id"])
    assert task["status"] == "succeeded" and task["result"]["kind"] == "evidence"

    history = signed.get(f"/api/v1/projects/{project}/papers/{paper_id}/evidence")
    assert history.status_code == 200
    items = history.json()["items"]
    assert len(items) == 1 and items[0]["paper_id"] == paper_id
    assert set(items[0]) >= {"research_question", "method", "results", "conclusion", "limitations"}


def test_search_hits_searchable_paper(signed, project):
    papers = signed.get(f"/api/v1/projects/{project}/papers").json()["items"]
    paper_id = papers[0]["paper_id"]
    response = signed.post(f"/api/v1/projects/{project}/search", json={"query": "合成", "mode": "hybrid"})
    assert response.status_code == 200
    hits = response.json()["items"]
    assert hits and hits[0]["paper"]["paper_id"] == paper_id
    assert hits[0]["paper"]["status"] == "searchable"


def test_outbox_deliverer_publishes_domain_events_to_audit(signed, project, stack):
    from app.data.deliverer import build_deliverer

    # B's HTTP audit trail uses its own action names; the outbox carries A's domain actions.
    before = signed.get(
        f"/api/v1/projects/{project}/audit-events", params={"action": "create_project"}
    ).json()["total"]
    assert before == 0

    deliverer = build_deliverer(stack.config)
    try:
        assert deliverer.run_once() >= 1
    finally:
        deliverer.database.close()

    after = signed.get(
        f"/api/v1/projects/{project}/audit-events", params={"action": "create_project"}
    )
    assert after.status_code == 200
    body = after.json()
    assert body["total"] >= 1 and body["items"][0]["project_id"] == project


def test_report_versions_conflict_snapshot_and_export(signed, project):
    papers = signed.get(f"/api/v1/projects/{project}/papers").json()["items"]
    paper_id = papers[0]["paper_id"]
    sources = signed.get(f"/api/v1/projects/{project}/papers/{paper_id}").json()["sources"]

    def report_payload(base=None, body_text="版本一 [1]"):
        payload = {
            "title": "业务联调报告",
            "research_question": "业务联调问题",
            "body": body_text,
            "sources": sources,
        }
        if base:
            payload.update(base)
        return payload

    first = signed.post(
        f"/api/v1/projects/{project}/reports",
        json=report_payload(),
        headers={"Idempotency-Key": "biz-report-1"},
    )
    assert first.status_code == 201, first.text
    version_one = first.json()
    assert version_one["number"] == 1

    replay = signed.post(
        f"/api/v1/projects/{project}/reports",
        json=report_payload(),
        headers={"Idempotency-Key": "biz-report-1"},
    )
    assert replay.status_code == 201
    assert replay.json()["version_id"] == version_one["version_id"]

    base = {"report_id": version_one["report_id"], "base_version_id": version_one["version_id"]}
    second = signed.post(
        f"/api/v1/projects/{project}/reports",
        json=report_payload(base, "版本二"),
        headers={"Idempotency-Key": "biz-report-2"},
    )
    assert second.status_code == 201
    version_two = second.json()
    assert version_two["number"] == 2 and version_two["report_id"] == version_one["report_id"]

    stale = signed.post(
        f"/api/v1/projects/{project}/reports",
        json=report_payload(base, "过期基础版本"),
        headers={"Idempotency-Key": "biz-report-stale"},
    )
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"

    reports = signed.get(f"/api/v1/projects/{project}/reports").json()["items"]
    assert [v["version_id"] for v in reports] == [version_two["version_id"]]
    versions = signed.get(
        f"/api/v1/projects/{project}/reports/{version_one['report_id']}/versions"
    ).json()["items"]
    assert [v["number"] for v in versions] == [1, 2]
    assert versions[0]["sources"] == sources

    exported = signed.get(
        f"/api/v1/projects/{project}/reports/{version_one['report_id']}"
        f"/versions/{version_one['version_id']}/text"
    )
    assert exported.status_code == 200 and exported.text == "版本一 [1]"


def test_member_lifecycle_scoping_and_audit(signed, project):
    added = signed.post(f"/api/v1/projects/{project}/members", json={"username": "bob"})
    assert added.status_code == 201 and added.json()["role"] == "member"
    repeat = signed.post(f"/api/v1/projects/{project}/members", json={"username": "bob"})
    assert repeat.status_code == 201 and repeat.json()["role"] == "member"
    members = signed.get(f"/api/v1/projects/{project}/members").json()["items"]
    assert len(members) == 2
    bob_id = next(m["user"]["user_id"] for m in members if m["user"]["username"] == "bob")

    # Switch the shared client to bob; always switch back to alice before returning.
    assert signed.post("/api/v1/auth/login", json={"username": "bob"}).status_code == 200
    assert signed.get("/api/v1/projects").status_code == 200
    assert signed.get(f"/api/v1/projects/{project}/papers").status_code == 200
    denied_member = signed.post(f"/api/v1/projects/{project}/members", json={"username": "eve"})
    assert denied_member.status_code == 403 and denied_member.json()["code"] == "OWNER_REQUIRED"
    denied_audit = signed.get(f"/api/v1/projects/{project}/audit-events")
    assert denied_audit.status_code == 403 and denied_audit.json()["code"] == "OWNER_REQUIRED"

    assert signed.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
    audit = signed.get(f"/api/v1/projects/{project}/audit-events", params={"limit": 100})
    assert audit.status_code == 200
    body = audit.json()
    assert body["total"] > 0 and body["items"]
    filtered = signed.get(
        f"/api/v1/projects/{project}/audit-events", params={"action": "paper.import_requested"}
    )
    assert filtered.status_code == 200 and filtered.json()["total"] >= 1

    removed = signed.delete(f"/api/v1/projects/{project}/members/{bob_id}")
    assert removed.status_code == 200
    me = signed.get("/api/v1/auth/me").json()["user"]["user_id"]
    owner_only = signed.delete(f"/api/v1/projects/{project}/members/{me}")
    assert owner_only.status_code == 409 and owner_only.json()["code"] == "OWNER_IMMUTABLE"

    assert signed.post("/api/v1/auth/login", json={"username": "bob"}).status_code == 200
    revoked = signed.get(f"/api/v1/projects/{project}/papers")
    assert revoked.status_code == 404 and revoked.json()["code"] == "RESOURCE_NOT_FOUND"
    assert signed.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200


def test_remove_paper_hides_live_keeps_report_history(signed, project):
    papers = signed.get(f"/api/v1/projects/{project}/papers").json()["items"]
    paper_id = papers[0]["paper_id"]
    snapshot = signed.get(f"/api/v1/projects/{project}/papers/{paper_id}").json()["sources"]
    reports = signed.get(f"/api/v1/projects/{project}/reports").json()["items"]
    report_id = reports[0]["report_id"]

    confirm = signed.delete(
        f"/api/v1/projects/{project}/papers/{paper_id}", params={"confirm_source_impact": "true"}
    )
    assert confirm.status_code == 200
    assert signed.get(f"/api/v1/projects/{project}/papers/{paper_id}").status_code == 404
    assert signed.get(f"/api/v1/projects/{project}/papers/{paper_id}/evidence").status_code == 404

    versions = signed.get(f"/api/v1/projects/{project}/reports/{report_id}/versions").json()["items"]
    assert versions[0]["sources"] == snapshot

    replay = upload(signed, project, key="biz-upload-1")
    assert replay.status_code == 404  # idempotent replay must not serve removed resources


def test_second_process_restart_preserves_data(client, stack):
    project_id = client.get("/api/v1/projects").json()["items"][0]["project_id"]
    papers = client.get(f"/api/v1/projects/{project_id}/papers").json()["items"]
    assert papers == []  # the single live paper was removed; only history remains below

    with TestClient(stack.new_app(), headers=WEB) as restarted:
        assert restarted.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        projects = restarted.get("/api/v1/projects").json()["items"]
        assert projects and projects[0]["mock"] is False
        project_id = projects[0]["project_id"]

        assert restarted.get(f"/api/v1/projects/{project_id}/papers").json()["items"] == []

        reports = restarted.get(f"/api/v1/projects/{project_id}/reports").json()["items"]
        assert reports and reports[0]["number"] == 2
        versions = restarted.get(
            f"/api/v1/projects/{project_id}/reports/{reports[0]['report_id']}/versions"
        ).json()["items"]
        assert [v["number"] for v in versions] == [1, 2]
        removed_paper = versions[0]["sources"][0]["paper_id"]
        assert (
            restarted.get(f"/api/v1/projects/{project_id}/papers/{removed_paper}").status_code == 404
        )

        replay = upload(restarted, project_id, key="biz-upload-1")
        assert replay.status_code == 404  # replay never bypasses removal, even after restart

        audit = restarted.get(f"/api/v1/projects/{project_id}/audit-events")
        assert audit.status_code == 200 and audit.json()["total"] > 0
