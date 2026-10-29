"""Critical audit ordering with controlled dependencies, not a production SQL executor."""

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.mock_data import MockData, seed_id
from app.adapters.ports import Context
from app.config import Settings
from app.errors import AppError
from app.main import create_app
from app.security import SecurityAdapter, build_security
from app.security.sql_policy import SQL_BY_QUESTION, prepare_query

QUESTION = "按年份统计论文数量"


@pytest.fixture
def data():
    return MockData()


@pytest.fixture
def ctx():
    return Context(seed_id("alice"), seed_id("project-a"), uuid4())


def events(data, ctx):
    return [event for event in data.events if event.request_id == ctx.request_id]


def test_execution_waits_for_committed_pre_audit_and_returns_after_post_audit(data, ctx):
    async def execute(actual_ctx, plan):
        assert actual_ctx == ctx
        assert plan == prepare_query(SQL_BY_QUESTION[QUESTION], ctx.project_id)
        recorded = events(data, ctx)
        assert len(recorded) == 1
        assert (recorded[0].action, recorded[0].execution_status) == ("security.query.prepared", "pending")
        return ["year", "count"], [{"year": 2024, "count": 2}]

    result = asyncio.run(SecurityAdapter(data, query_executor=execute).execute(ctx, QUESTION))
    assert (result.decision, result.execution_status) == ("allowed", "executed")
    assert result.rows == [{"year": 2024, "count": 2}]
    recorded = events(data, ctx)
    assert len(recorded) == 2
    assert (recorded[1].action, recorded[1].execution_status) == ("security.query.finished", "executed")
    for event in recorded:
        assert event.request_id == ctx.request_id
        assert (event.actor_id, event.project_id) == (ctx.user_id, ctx.project_id)
        assert event.resource_type == "secure_query" and event.policy_id == result.policy_id
        assert event.mock is False and event.duration_ms >= 0 and event.timestamp.tzinfo is not None
        assert event.event_id != ctx.request_id
    assert recorded[0].event_id != recorded[1].event_id


@pytest.mark.parametrize("stage", ["prepared", "finished"])
@pytest.mark.parametrize("committed", [False, True])
def test_unconfirmed_audit_never_returns_rows_or_retries_query(data, ctx, monkeypatch, stage, committed):
    append = data.append_audit
    calls = 0

    async def execute(*args):
        nonlocal calls
        calls += 1
        return ["title"], [{"title": "PRIVATE_RESULT"}]

    def fail(event):
        if event.action == f"security.query.{stage}":
            if committed:
                append(event)
            raise AppError(503, "PRIVATE_AUDIT", "postgres://PRIVATE_PASSWORD")
        append(event)

    monkeypatch.setattr(data, "append_audit", fail)
    with pytest.raises(AppError) as error:
        asyncio.run(SecurityAdapter(data, query_executor=execute).execute(ctx, QUESTION))
    assert (error.value.status, error.value.code) == (503, "AUDIT_UNAVAILABLE")
    assert "PRIVATE" not in error.value.message
    assert "未执行" not in error.value.message
    assert calls == (stage == "finished")
    assert len(events(data, ctx)) == (stage == "finished") + committed


@pytest.mark.parametrize(
    "failure,code",
    [
        (RuntimeError("PRIVATE_SQL"), "QUERY_EXECUTION_FAILED"),
        (AppError(500, "PRIVATE", "postgres://PRIVATE_PASSWORD"), "QUERY_EXECUTION_FAILED"),
        (TimeoutError("PRIVATE_SQL"), "QUERY_TIMEOUT"),
    ],
)
def test_execution_failure_is_audited_and_sanitized(data, ctx, failure, code):
    async def execute(*args):
        raise failure

    result = asyncio.run(SecurityAdapter(data, query_executor=execute).execute(ctx, QUESTION))
    assert (result.decision, result.execution_status, result.reason.code) == ("undetermined", "failed", code)
    assert not result.rows and not result.columns
    assert [event.execution_status for event in events(data, ctx)] == ["pending", "failed"]
    assert "PRIVATE" not in result.model_dump_json()
    assert "PRIVATE" not in "".join(event.model_dump_json() for event in events(data, ctx))


@pytest.mark.parametrize("output", [None, ([], [{"title": object()}]), (["year"], "PRIVATE_RESULT")])
def test_malformed_executor_result_is_audited_as_failure(data, ctx, output):
    async def execute(*args):
        return output

    result = asyncio.run(SecurityAdapter(data, query_executor=execute).execute(ctx, QUESTION))
    assert result.reason.code == "QUERY_EXECUTION_FAILED" and not result.rows and not result.columns
    assert [event.execution_status for event in events(data, ctx)] == ["pending", "failed"]


@pytest.mark.parametrize("unavailable", [False, True])
def test_task_cancellation_propagates_and_never_returns_rows(data, ctx, monkeypatch, unavailable):
    append = data.append_audit
    attempts = []
    cleaned = False

    def audit(event):
        attempts.append(event)
        if unavailable and event.execution_status == "cancelled":
            raise RuntimeError("PRIVATE_AUDIT")
        append(event)

    async def scenario():
        started = asyncio.Event()

        async def execute(*args):
            nonlocal cleaned
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned = True

        task = asyncio.create_task(SecurityAdapter(data, query_executor=execute).execute(ctx, QUESTION))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    monkeypatch.setattr(data, "append_audit", audit)
    asyncio.run(scenario())
    assert cleaned
    assert [event.execution_status for event in attempts] == ["pending", "cancelled"]
    assert all(event.execution_status != "executed" for event in events(data, ctx))


@pytest.mark.parametrize("stage", ["audit", "execution"])
def test_revocation_during_audit_or_execution_blocks_results(data, ctx, monkeypatch, stage):
    member = Context(seed_id("bob"), ctx.project_id, ctx.request_id)
    append = data.append_audit
    calls = 0

    def audit(event):
        append(event)
        if stage == "audit" and event.action == "security.query.prepared":
            data.remove_member(ctx, member.user_id)

    async def execute(*args):
        nonlocal calls
        calls += 1
        data.remove_member(ctx, member.user_id)
        return ["title"], [{"title": "PRIVATE_RESULT"}]

    monkeypatch.setattr(data, "append_audit", audit)
    with pytest.raises(AppError) as error:
        asyncio.run(SecurityAdapter(data, query_executor=execute).execute(member, QUESTION))
    assert error.value.code == "RESOURCE_NOT_FOUND"
    assert calls == (stage == "execution")
    assert [event.execution_status for event in events(data, ctx)] == ["pending", "failed"]


@pytest.mark.parametrize("question,status", [(QUESTION, "allowed"), ("PRIVATE_QUESTION", "rejected")])
def test_disabled_and_rejected_paths_record_request_without_candidate_sql(data, ctx, question, status):
    result = asyncio.run(build_security(data).execute(ctx, question))
    recorded = events(data, ctx)
    assert (result.decision, result.execution_status) == (status, "not_executed")
    assert len(recorded) == 1 and recorded[0].execution_status == "not_executed"
    assert recorded[0].decision == status
    assert recorded[0].details.question == question
    assert (recorded[0].details.sql is not None) == (status == "allowed")


def test_policy_rejection_never_passes_sql_to_audit_or_executor(data, ctx):
    async def generate(*args):
        return "SELECT PRIVATE_PASSWORD FROM users"

    async def execute(*args):
        pytest.fail("Rejected candidates must never reach the executor")

    result = asyncio.run(
        SecurityAdapter(data, candidate_generator=generate, query_executor=execute).execute(ctx, QUESTION)
    )
    assert result.reason.code == "SQL_POLICY_REJECTED"
    assert len(events(data, ctx)) == 1
    assert "PRIVATE_PASSWORD" not in events(data, ctx)[0].model_dump_json()


def test_candidate_failure_has_a_failed_event_without_private_diagnostics(data, ctx):
    async def generate(*args):
        raise RuntimeError("PRIVATE_MODEL_CREDENTIALS")

    with pytest.raises(AppError) as error:
        asyncio.run(SecurityAdapter(data, candidate_generator=generate).execute(ctx, QUESTION))
    assert error.value.code == "GATEWAY_UNAVAILABLE"
    assert [event.execution_status for event in events(data, ctx)] == ["failed"]
    assert "PRIVATE" not in events(data, ctx)[0].model_dump_json()


def test_same_request_id_does_not_deduplicate_distinct_attempts(data, ctx):
    asyncio.run(build_security(data).execute(ctx, QUESTION))
    asyncio.run(build_security(data).execute(ctx, QUESTION))
    recorded = events(data, ctx)
    assert len(recorded) == 2 and recorded[0].event_id != recorded[1].event_id
    data.append_audit(recorded[0])
    assert len(events(data, ctx)) == 2


def test_revocation_during_completion_audit_still_prevents_returning_rows(data, ctx, monkeypatch):
    member = Context(seed_id("bob"), ctx.project_id, ctx.request_id)
    append = data.append_audit

    async def execute(*args):
        return ["year", "count"], [{"year": 2024, "count": 1}]

    def audit(event):
        append(event)
        if event.action == "security.query.finished":
            data.remove_member(ctx, member.user_id)

    monkeypatch.setattr(data, "append_audit", audit)
    with pytest.raises(AppError) as error:
        asyncio.run(SecurityAdapter(data, query_executor=execute).execute(member, QUESTION))
    assert error.value.code == "RESOURCE_NOT_FOUND"
    # The event describes actual SQL completion, not acknowledgment or result delivery.
    assert [event.execution_status for event in events(data, ctx)] == ["pending", "executed"]


@pytest.mark.parametrize("stage", ["prepared", "finished"])
def test_http_business_audit_cannot_replace_d_gate(tmp_path, monkeypatch, stage):
    calls = 0

    async def execute(*args):
        nonlocal calls
        calls += 1
        return ["title"], [{"title": "PRIVATE_RESULT"}]

    app = create_app(
        Settings(environment="test", d_mode="real", storage_dir=tmp_path / "uploads"),
        security_factory=lambda data: SecurityAdapter(data, query_executor=execute),
    )
    data = app.state.adapters.data
    append = data.append_audit

    def audit(event):
        if event.action == f"security.query.{stage}":
            raise RuntimeError("PRIVATE_AUDIT_PASSWORD")
        append(event)

    with TestClient(app, headers={"X-PaperPilot-Client": "web"}) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        monkeypatch.setattr(data, "append_audit", audit)
        request_id = str(uuid4())
        response = client.post(
            f"/api/v1/projects/{seed_id('project-a')}/secure-queries",
            json={"question": QUESTION},
            headers={"X-Request-ID": request_id},
        )
        assert response.status_code == 503 and response.json()["code"] == "AUDIT_UNAVAILABLE"
        assert response.json()["request_id"] == request_id and "PRIVATE" not in response.text
        assert "rows" not in response.json() and calls == (stage == "finished")
        actions = [event.action for event in data.events if str(event.request_id) == request_id]
        assert "secure_query.requested" in actions and "secure_query.finished" in actions


@pytest.mark.parametrize("executor", [False, "not callable"])
def test_executor_misconfiguration_fails_at_construction(data, executor):
    with pytest.raises(ValueError):
        SecurityAdapter(data, query_executor=executor)
