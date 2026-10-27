"""Behavior of opt-in synthetic seeds; no persistent database or AI assertions."""

import pytest
from fastapi.testclient import TestClient

from app.adapters.mock_data import seed_id
from app.config import Settings
from app.demo import DEMO_PROJECT_ID, DEMO_REPORT_TITLE, DEMO_TITLE, create_demo_app
from app.main import create_app

HEADERS = {"X-PaperPilot-Client": "web"}
BASE = f"/api/v1/projects/{DEMO_PROJECT_ID}"


@pytest.fixture
def demo(tmp_path):
    app = create_demo_app(Settings(environment="test", storage_dir=tmp_path / "uploads"))
    with TestClient(app, headers=HEADERS) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        yield app, client


def test_standard_entry_keeps_the_minimal_fixture(tmp_path):
    app = create_app(Settings(environment="test", storage_dir=tmp_path / "uploads"))
    assert create_demo_app(Settings(environment="test")).openapi() == app.openapi()
    with TestClient(app, headers=HEADERS) as client:
        client.post("/api/v1/auth/login", json={"username": "alice"})
        projects = client.get("/api/v1/projects").json()["items"]
        assert len(projects) == 1 and all(p["name"] != DEMO_TITLE for p in projects)
        base = f"/api/v1/projects/{projects[0]['project_id']}"
        assert len(client.get(base + "/papers").json()["items"]) == 1
        assert client.get(base + "/reports").json()["items"] == []


@pytest.mark.parametrize(
    "options",
    [
        {"environment": "production"},
        {"a_mode": "persistent"},
        {"a_mode": "real"},
        {"c_mode": "real"},
        {"d_mode": "real"},
    ],
)
def test_demo_refuses_production_and_any_real_adapter(options):
    with pytest.raises(RuntimeError, match="Demo entry requires"):
        create_demo_app(Settings(**options))


def test_seeded_papers_have_realistic_filters_and_explicit_synthetic_sources(demo):
    _, client = demo
    papers = client.get(BASE + "/papers").json()["items"]
    assert len(papers) == 6
    assert sum(p["status"] == "searchable" for p in papers) == 5
    assert sum(p["status"] == "failed" for p in papers) == 1
    assert all(p["mock"] and "合成演示" in p["title"] for p in papers)
    filtered = client.get(BASE + "/papers", params={"year_from": 2023, "year_to": 2024}).json()["items"]
    assert len(filtered) == 2 and {p["year"] for p in filtered} == {2023, 2024}
    search = client.post(BASE + "/search", json={"query": "证据链", "limit": 50}).json()
    assert len(search["items"]) == 10
    for hit in search["items"]:
        assert hit["source"]["synthetic"] and hit["source"]["page_number"] > 0
        assert "合成演示片段，非真实论文" in hit["source"]["excerpt"]
        assert hit["source"]["paper_id"] == hit["paper"]["paper_id"]


def test_saved_evidence_covers_supported_missing_and_insufficient_without_regeneration(demo):
    _, client = demo
    paper_id = seed_id("demo-v1/paper/rag")
    path = BASE + f"/papers/{paper_id}/evidence"
    first = client.get(path).json()["items"]
    assert len(first) == 1 and first[0]["mock"]
    evidence = first[0]
    assert evidence["method"]["status"] == "pending_verification" and evidence["method"]["sources"]
    assert evidence["results"]["status"] == "insufficient_evidence" and not evidence["results"]["sources"]
    assert evidence["limitations"]["status"] == "not_mentioned" and evidence["limitations"]["text"] is None
    assert client.get(path).json()["items"] == first


def test_report_versions_and_snapshots_are_distinct_and_can_continue_editing(demo):
    _, client = demo
    latest = client.get(BASE + "/reports").json()["items"]
    assert len(latest) == 1 and latest[0]["title"] == DEMO_REPORT_TITLE
    path = BASE + f"/reports/{latest[0]['report_id']}/versions"
    old = client.get(path).json()["items"]
    assert [v["number"] for v in old] == [1, 2]
    assert len(old[0]["sources"]) == 2 and len(old[1]["sources"]) == 3
    assert old[0]["body"] != old[1]["body"]
    for version in old:
        assert version["mock"] and "全文为合成演示资料" in version["body"]
        for source in version["sources"]:
            detail = client.get(BASE + f"/papers/{source['paper_id']}").json()
            assert any(
                s["chunk_id"] == source["chunk_id"] and s["excerpt"] == source["excerpt"]
                for s in detail["sources"]
            )
    response = client.post(
        BASE + "/reports",
        headers={"Idempotency-Key": "demo-test-v3"},
        json={
            "title": latest[0]["title"],
            "research_question": latest[0]["research_question"],
            "body": "第三版手工演示编辑",
            "sources": latest[0]["sources"],
            "report_id": latest[0]["report_id"],
            "base_version_id": latest[0]["version_id"],
        },
    )
    assert response.status_code == 201 and response.json()["number"] == 3
    assert client.get(path).json()["items"][:2] == old


def test_failed_task_is_queryable_and_only_explicit_retry_reprocesses_it(demo):
    app, client = demo
    task_id = app.state.demo_manifest["failed_task_id"]
    path = BASE + f"/tasks/{task_id}"
    task = client.get(path).json()
    assert task["status"] == "failed" and task["attempts"] == 1
    assert task["error"]["code"] == "AI_UNAVAILABLE" and "合成演示" in task["error"]["message"]
    assert task["result"] is None and task["result_ref"] is None
    assert client.get(path).json() == task
    assert client.post(path + "/retry").status_code == 202
    from conftest import finished

    completed = finished(client, DEMO_PROJECT_ID, task_id)
    assert completed["status"] == "succeeded" and completed["attempts"] == 2
    assert completed["result"]["mock"]


def test_demo_audits_come_from_mock_queries_and_statistics_match_papers(demo):
    _, client = demo
    events = client.get(BASE + "/audit-events", params={"limit": 100}).json()["items"]
    queries = [e for e in events if e["action"] == "secure_query.finished"]
    assert {(e["decision"], e["execution_status"]) for e in queries} == {
        ("allowed", "executed"),
        ("rejected", "not_executed"),
        ("undetermined", "failed"),
    }
    assert all(e["mock"] and e["policy_id"] == "mock-intents-v1" for e in queries)
    for event in queries:
        assert any(
            e["action"] == "secure_query.requested" and e["request_id"] == event["request_id"] for e in events
        )
    stats = client.post(BASE + "/secure-queries", json={"question": "按年份统计论文数量"}).json()
    assert stats["mock"] and sum(row["count"] for row in stats["rows"]) == 6
    missing = client.post(BASE + "/secure-queries", json={"question": "查询尚未完成证据提取的论文"}).json()
    assert len(missing["rows"]) == 4


def test_demo_resources_still_require_login_membership_and_owner_audit(demo):
    app, client = demo
    task_id = app.state.demo_manifest["failed_task_id"]
    for username, expected in (("bob", 200), ("eve", 404)):
        client.post("/api/v1/auth/login", json={"username": username})
        assert client.get(BASE + "/papers").status_code == expected
        assert client.get(BASE + "/reports").status_code == expected
        assert client.get(BASE + f"/tasks/{task_id}").status_code == expected
        assert client.get(BASE + f"/papers/{seed_id('demo-v1/paper/rag')}/evidence").status_code == expected
        assert client.get(BASE + "/audit-events").status_code == (403 if username == "bob" else 404)
    client.post("/api/v1/auth/logout")
    assert client.get(BASE + "/papers").status_code == 401


def test_demo_reinitializes_only_fresh_memory_and_does_not_duplicate_within_app(tmp_path):
    settings = Settings(environment="test", storage_dir=tmp_path / "uploads")
    app = create_demo_app(settings)
    for _ in range(2):
        with TestClient(app, headers=HEADERS) as client:
            client.post("/api/v1/auth/login", json={"username": "alice"})
            assert len(client.get(BASE + "/papers").json()["items"]) == 6
            assert client.get(BASE + "/reports").json()["items"][0]["number"] == 2
    fresh = create_demo_app(settings)
    with TestClient(fresh, headers=HEADERS) as client:
        client.post("/api/v1/auth/login", json={"username": "alice"})
        assert len(client.get(BASE + "/papers").json()["items"]) == 6
        assert client.get(BASE + "/reports").json()["items"][0]["number"] == 2
    # Metadata fixtures never manufacture a PDF or touch an existing upload directory.
    assert not (tmp_path / "uploads").exists()
