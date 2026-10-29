"""B task recovery/auth/idempotency behavior using Mock, no persistence claims."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.mock_data import seed_id
from app.adapters.ports import Context
from app.schemas import QuestionRequest


def context():
    return Context(seed_id("alice"), seed_id("project-a"), uuid4())


def base():
    return f"/api/v1/projects/{seed_id('project-a')}"


def pending(data):
    return data.create_task(
        context(),
        "answer",
        {"question": QuestionRequest(question="授权任务"), "paper_ids": [seed_id("paper-a")]},
        "recovery-test",
    )


def test_worker_limits_total_task_time_without_starting_another_task(app, monkeypatch):
    import asyncio
    import time

    task = pending(app.state.adapters.data)
    app.state.service.job_timeout_seconds = 0.05
    calls = []

    async def stalled_search(*args):
        calls.append("started")
        await asyncio.sleep(5)

    monkeypatch.setattr(app.state.adapters.ai, "search", stalled_search)
    with TestClient(app, headers={"X-PaperPilot-Client": "web"}) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        for _ in range(100):
            result = client.get(base() + f"/tasks/{task.task_id}").json()
            if result["status"] == "failed":
                break
            time.sleep(0.02)
        assert result["status"] == "failed"
        assert result["error"]["code"] == "TASK_TIMEOUT"
        assert calls == ["started"]
        assert result["task_id"] == str(task.task_id)


def test_health_reports_known_worker_failure_and_recovers(app):
    client = TestClient(app)
    app.state.service.worker_claim_unavailable = True
    failed = client.get("/api/v1/health")
    assert failed.status_code == 503 and failed.json()["code"] == "WORKER_UNAVAILABLE"
    app.state.service.worker_claim_unavailable = False
    assert client.get("/api/v1/health").status_code == 200
    client.close()


def signed_client(app, name="alice"):
    # Deliberately no lifespan: a GET must not start or claim work itself.
    client = TestClient(app, headers={"X-PaperPilot-Client": "web"})
    assert client.post("/api/v1/auth/login", json={"username": name}).status_code == 200
    return client


def test_repeated_task_get_is_read_only_and_does_not_execute_ai(app, monkeypatch):
    data = app.state.adapters.data
    created = pending(data)
    before = data.task(context(), created.task_id).model_dump(mode="json")

    def forbidden(*args, **kwargs):
        raise AssertionError("GET must not execute or mutate")

    for name in ("claim_job", "create_task", "complete_job", "fail_job", "retry_task"):
        monkeypatch.setattr(data, name, forbidden)
    for name in ("process", "search", "answer", "extract", "draft"):
        monkeypatch.setattr(app.state.adapters.ai, name, forbidden)
    client = signed_client(app)
    for _ in range(3):
        response = client.get(base() + f"/tasks/{created.task_id}")
        assert response.status_code == 200 and response.json() == before
    assert data.task(context(), created.task_id).model_dump(mode="json") == before
    client.close()


def test_pending_and_completed_task_reads_preserve_state(app, monkeypatch):
    import asyncio

    data = app.state.adapters.data
    created = pending(data)
    client = signed_client(app)
    path = base() + f"/tasks/{created.task_id}"
    assert client.get(path).json()["status"] == "pending"
    asyncio.run(app.state.service.run_job(data.claim_job()))
    before = client.get(path).json()
    assert before["status"] == "succeeded" and before["result"]["kind"] == "answer"

    def forbidden(*args, **kwargs):
        raise AssertionError("GET must not mutate")

    for name in ("claim_job", "create_task", "complete_job", "fail_job", "retry_task"):
        monkeypatch.setattr(data, name, forbidden)
    monkeypatch.setattr(app.state.adapters.ai, "answer", forbidden)
    for _ in range(4):
        assert client.get(path).json() == before
    client.close()


def test_task_recovery_denies_anonymous_outsider_and_cross_project(app):
    data = app.state.adapters.data
    created = pending(data)
    job = data.claim_job()
    data.fail_job(job, "PRIVATE_TASK_ERROR", "仅授权项目可见的任务错误")
    path = base() + f"/tasks/{created.task_id}"
    anonymous = TestClient(app)
    outsider = signed_client(app, "eve")
    owner = signed_client(app)
    for response in (
        anonymous.get(path),
        outsider.get(path),
        outsider.get(f"/api/v1/projects/{seed_id('project-b')}/tasks/{created.task_id}"),
    ):
        assert response.status_code in (401, 404)
        assert "PRIVATE_TASK_ERROR" not in response.text and "仅授权项目" not in response.text
    missing = owner.get(base() + f"/tasks/{uuid4()}")
    assert missing.status_code == 404 and missing.json()["code"] == "RESOURCE_NOT_FOUND"
    assert owner.get(path).json()["error"]["code"] == "PRIVATE_TASK_ERROR"
    anonymous.close()
    outsider.close()
    owner.close()


def test_revoked_member_cannot_recover_previous_task(app):
    created = pending(app.state.adapters.data)
    member = signed_client(app, "bob")
    path = base() + f"/tasks/{created.task_id}"
    assert member.get(path).status_code == 200
    app.state.adapters.data.remove_member(context(), seed_id("bob"))
    assert member.get(path).status_code == 404
    member.close()


@pytest.mark.parametrize("operation", ["task", "role"])
def test_query_unavailable_is_safe_and_has_no_mock_fallback(app, monkeypatch, operation):
    created = pending(app.state.adapters.data)
    client = signed_client(app)

    def unavailable(*args):
        raise RuntimeError("PRIVATE_PROVIDER_CONNECTION")

    monkeypatch.setattr(app.state.adapters.data, operation, unavailable)
    response = client.get(base() + f"/tasks/{created.task_id}")
    assert response.status_code == 503 and response.json()["code"] == "TASK_QUERY_UNAVAILABLE"
    assert "PRIVATE_PROVIDER_CONNECTION" not in response.text
    client.close()


@pytest.mark.parametrize("kind", ["process", "answer", "evidence", "draft"])
def test_each_creation_flow_replays_same_key_without_new_task(app, kind):
    client = signed_client(app)
    key = {"Idempotency-Key": "fixed-recovery-key"}
    if kind == "process":
        path = base() + "/papers"
        kwargs = {
            "files": {"file": ("test.pdf", b"%PDF-1.4\nIdempotent recovery")},
            "data": {"title": "契约恢复"},
        }
    else:
        suffix = {
            "answer": "/answers",
            "draft": "/report-drafts",
            "evidence": f"/papers/{seed_id('paper-a')}/evidence",
        }[kind]
        path = base() + suffix
        kwargs = (
            {}
            if kind == "evidence"
            else {"json": {"question": "同一恢复请求", "paper_ids": [str(seed_id("paper-a"))]}}
        )
    first = client.post(path, headers=key, **kwargs)
    second = client.post(path, headers=key, **kwargs)
    assert first.status_code == second.status_code == 202
    task = lambda response: response.json()["task"] if kind == "process" else response.json()  # noqa: E731
    assert task(first)["task_id"] == task(second)["task_id"]
    assert task(second)["attempts"] == 0
    client.close()


def test_unacknowledged_creation_replays_existing_task_after_safe_error(app, monkeypatch):
    client = signed_client(app)
    original = app.state.adapters.data.create_task

    def commit_without_ack(*args):
        original(*args)
        raise RuntimeError("PRIVATE_COMMIT_ACK_DETAIL")

    monkeypatch.setattr(app.state.adapters.data, "create_task", commit_without_ack)
    kwargs = {
        "json": {"question": "核对", "paper_ids": [str(seed_id("paper-a"))]},
        "headers": {"Idempotency-Key": "lost-ack"},
    }
    first = client.post(base() + "/answers", **kwargs)
    assert first.status_code == 503 and first.json()["code"] == "DATA_COMMIT_UNKNOWN"
    assert "PRIVATE_COMMIT_ACK_DETAIL" not in first.text
    monkeypatch.setattr(app.state.adapters.data, "create_task", original)
    recovered = client.post(base() + "/answers", **kwargs)
    assert recovered.status_code == 202
    assert client.get(base() + f"/tasks/{recovered.json()['task_id']}").json()["status"] == "pending"
    client.close()
