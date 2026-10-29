"""Operation details remain useful, bounded and separate from private diagnostics."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.data_rules import canonical
from app.adapters.mock_data import MockData, seed_id
from app.adapters.ports import Context
from app.config import Settings
from app.main import create_app
from app.schemas import AuditDetails, AuditEvent
from app.security import SecurityAdapter
from app.security.sql_policy import SQL_BY_QUESTION, prepare_query


@pytest.mark.parametrize(
    "text",
    [
        "password=hunter2",
        "密码：不要记录",
        "Bearer abc123",
        'api_key="abc123"',
        "https://user:password@example.test/path",
        "-----BEGIN PRIVATE KEY-----",
        "SESSION_TOKEN=abc",
        "Authorization: Basic abc",
        "Cookie: session=abc",
    ],
)
def test_details_hide_whole_sensitive_text(text):
    details = AuditDetails(question=text, resource_name=text)
    assert text not in details.model_dump_json()
    assert details.question.startswith("[已隐藏")
    assert details.resource_name == details.question


def test_legacy_audit_canonical_bytes_are_unchanged():
    event = AuditEvent(
        event_id=uuid4(),
        timestamp=datetime.now(UTC),
        request_id=uuid4(),
        actor_id=None,
        project_id=None,
        action="legacy",
        resource_type="test",
        decision="allowed",
        execution_status="completed",
        duration_ms=0,
    )
    assert json.loads(canonical(event)) == event.model_dump(mode="json", exclude={"details"})


def test_only_rebuilt_approved_sql_is_recorded_per_request():
    data = MockData()
    owner = Context(seed_id("alice"), seed_id("project-a"), uuid4())
    seen = []

    async def execute(ctx, plan):
        seen.append(plan.sql)
        return ["year", "count"], [{"year": 2024, "count": 1}]

    adapter = SecurityAdapter(data, query_executor=execute)
    question = "按年份统计论文数量"
    result = asyncio.run(adapter.execute(owner, question))
    assert result.execution_status == "executed"
    expected = prepare_query(SQL_BY_QUESTION[question], owner.project_id).sql
    events = [e for e in data.events if e.request_id == owner.request_id]
    assert len(events) == 2 and all(e.details.sql == expected for e in events)
    assert str(owner.project_id) not in expected and "%(project_id)s" in expected
    rejected = Context(owner.user_id, owner.project_id, uuid4())
    asyncio.run(adapter.execute(rejected, "unsupported question"))
    assert [e for e in data.events if e.request_id == rejected.request_id][0].details.sql is None
    assert seen == [expected]


def test_completed_document_task_records_its_actual_paper(tmp_path):
    app = create_app(Settings(environment="test", auth_mode="development", storage_dir=tmp_path))
    data = app.state.adapters.data
    ctx = Context(seed_id("alice"), seed_id("project-a"), uuid4())
    from app.schemas import QuestionRequest

    paper = data.papers(ctx)[0]
    app.state.service.question_task(
        ctx, "evidence", QuestionRequest(question="证据提取", paper_ids=[paper.paper_id]), uuid4().hex
    )
    job = data.claim_job()
    assert job is not None and job.task.kind == "evidence"
    asyncio.run(app.state.service.run_job(job))
    completed = next(
        e for e in data.events if e.action == "evidence.completed" and e.request_id == ctx.request_id
    )
    assert completed.details.resource_ids == [paper.paper_id]


def test_api_details_capture_query_and_removed_document_and_preserve_owner_boundary(tmp_path):
    app = create_app(Settings(environment="test", auth_mode="development", storage_dir=tmp_path))
    base = f"/api/v1/projects/{seed_id('project-a')}"
    with TestClient(app, headers={"X-PaperPilot-Client": "web"}) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        question = "按年份统计论文数量"
        query = client.post(base + "/secure-queries", json={"question": question}).json()
        paper = client.get(base + "/papers").json()["items"][0]
        assert (
            client.delete(
                base + "/papers/" + paper["paper_id"], params={"confirm_source_impact": True}
            ).status_code
            == 200
        )
        rows = client.get(base + "/audit-events", params={"limit": 100}).json()["items"]
        queried = [r for r in rows if r["request_id"] == query["request_id"]]
        assert all(r["details"]["question"] == question for r in queried)
        removed = next(r for r in rows if r["action"] == "paper.removed")
        assert removed["details"]["resource_ids"] == [paper["paper_id"]]
        assert removed["details"]["resource_name"] == paper["title"]
        client.post("/api/v1/auth/login", json={"username": "bob"})
        assert client.get(base + "/audit-events").status_code == 403
        client.post("/api/v1/auth/login", json={"username": "eve"})
        assert client.get(base + "/audit-events").status_code == 404
