"""Task 1 adapter integration, not SQL attack protection or database permission evidence."""

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.factory import create_adapters
from app.adapters.mock_data import MockData, seed_id
from app.adapters.mock_security import MockSecurity
from app.adapters.ports import Context
from app.config import Settings
from app.errors import AppError
from app.schemas import SecureQueryResult
from app.security import SecurityAdapter, build_security
from app.security.main import create_app


@pytest.fixture
def data():
    return MockData()


@pytest.fixture
def ctx():
    return Context(seed_id("alice"), seed_id("project-a"), uuid4())


@pytest.mark.parametrize(
    "action,role",
    [
        ("project.read", "owner"),
        ("project.read", "member"),
        ("members.manage", "owner"),
        ("paper.remove", "owner"),
        ("audit.read", "owner"),
    ],
)
def test_known_permissions(data, ctx, action, role):
    if role == "member":
        ctx = Context(seed_id("bob"), ctx.project_id, ctx.request_id)
    assert build_security(data).authorize(ctx, action, role) is None


@pytest.mark.parametrize("action", ["members.manage", "paper.remove", "audit.read"])
def test_member_cannot_use_owner_permissions(data, ctx, action):
    ctx = Context(seed_id("bob"), ctx.project_id, ctx.request_id)
    with pytest.raises(AppError) as error:
        build_security(data).authorize(ctx, action, "member")
    assert (error.value.status, error.value.code) == (403, "OWNER_REQUIRED")


@pytest.mark.parametrize("role", ["owner", "member"])
def test_unknown_action_is_denied(data, ctx, role):
    if role == "member":
        ctx = Context(seed_id("bob"), ctx.project_id, ctx.request_id)
    with pytest.raises(AppError) as error:
        build_security(data).authorize(ctx, "unknown.action", role)
    assert (error.value.status, error.value.code) == (403, "ACTION_FORBIDDEN")


@pytest.mark.parametrize("role", ["admin", "", None])
def test_invalid_role_cannot_read_project(data, ctx, role):
    with pytest.raises(AppError) as error:
        build_security(data).authorize(ctx, "project.read", role)
    assert (error.value.status, error.value.code) == (404, "RESOURCE_NOT_FOUND")


@pytest.mark.parametrize(
    "question,decision,code",
    [
        ("按年份统计论文数量", "allowed", "QUERY_EXECUTION_DISABLED"),
        ("不支持的问题", "rejected", "UNSUPPORTED_QUERY"),
        ("模拟执行失败", "rejected", "UNSUPPORTED_QUERY"),
        ("模拟网关故障", "rejected", "UNSUPPORTED_QUERY"),
        ("SELECT * FROM papers", "rejected", "UNSUPPORTED_QUERY"),
    ],
)
def test_execute_never_runs_statistics_or_mock_fallback(data, ctx, monkeypatch, question, decision, code):
    def unexpected(*args, **kwargs):
        pytest.fail("The bootstrap adapter must not execute queries or fall back to Mock")

    monkeypatch.setattr(data, "controlled_statistics", unexpected)
    monkeypatch.setattr(MockSecurity, "execute", unexpected)
    result = asyncio.run(build_security(data).execute(ctx, question))
    assert isinstance(result, SecureQueryResult)
    assert (result.decision, result.execution_status) == (decision, "not_executed")
    assert result.reason.code == code
    assert result.policy_id == SecurityAdapter.policy_id and result.request_id == ctx.request_id
    assert result.mock is False and not result.rows and not result.columns


@pytest.mark.parametrize("user", ["bob", "eve"])
def test_execute_rechecks_membership(data, ctx, user):
    if user == "bob":
        data.memberships.pop((ctx.project_id, seed_id(user)))
    context = Context(seed_id(user), ctx.project_id, ctx.request_id)
    with pytest.raises(AppError) as error:
        asyncio.run(build_security(data).execute(context, "按年份统计论文数量"))
    assert (error.value.status, error.value.code) == (404, "RESOURCE_NOT_FOUND")


def test_authorization_dependency_failure_is_safe(data, ctx, monkeypatch):
    def unavailable(*args):
        raise RuntimeError("PRIVATE_DATABASE_CONFIG")

    monkeypatch.setattr(data, "role", unavailable)
    with pytest.raises(AppError) as error:
        asyncio.run(build_security(data).execute(ctx, "按年份统计论文数量"))
    assert (error.value.status, error.value.code) == (503, "GATEWAY_UNAVAILABLE")
    assert "PRIVATE_DATABASE_CONFIG" not in error.value.message


def test_factory_registers_adapter_with_shared_data():
    adapters = create_adapters(Settings(environment="test", d_mode="real"), security_factory=build_security)
    assert type(adapters.security) is SecurityAdapter
    assert adapters.security.data is adapters.data
    assert not isinstance(adapters.security, MockSecurity)


def test_construction_failure_never_falls_back(monkeypatch):
    def broken(data):
        raise RuntimeError("PRIVATE_SECURITY_CONFIG")

    monkeypatch.setattr("app.security.adapter.SecurityAdapter", broken)
    with pytest.raises(RuntimeError, match="D adapter initialization failed; no Mock fallback") as error:
        create_adapters(Settings(d_mode="real"), security_factory=build_security)
    assert "PRIVATE_SECURITY_CONFIG" not in str(error.value)


def test_composition_requires_explicit_real_mode(monkeypatch):
    monkeypatch.setenv("PAPERPILOT_ENV", "test")
    monkeypatch.setenv("PAPERPILOT_A_MODE", "mock")
    monkeypatch.setenv("PAPERPILOT_C_MODE", "mock")
    monkeypatch.setenv("PAPERPILOT_D_MODE", "mock")
    with pytest.raises(RuntimeError, match="Explicit D Mock mode"):
        create_app()


def test_d_composition_reaches_http_without_changing_public_contract(monkeypatch, tmp_path):
    for key, value in {
        "PAPERPILOT_ENV": "test",
        "PAPERPILOT_A_MODE": "mock",
        "PAPERPILOT_C_MODE": "mock",
        "PAPERPILOT_D_MODE": "real",
        "PAPERPILOT_STORAGE_DIR": str(tmp_path / "uploads"),
    }.items():
        monkeypatch.setenv(key, value)
    app = create_app()
    assert type(app.state.adapters.security) is SecurityAdapter
    with TestClient(app, headers={"X-PaperPilot-Client": "web"}) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        project = client.get("/api/v1/projects").json()["items"][0]["project_id"]
        assert client.get(f"/api/v1/projects/{project}/papers").status_code == 200
        request_id = str(uuid4())
        response = client.post(
            f"/api/v1/projects/{project}/secure-queries",
            json={"question": "按年份统计论文数量"},
            headers={"X-Request-ID": request_id},
        )
        assert response.status_code == 200
        result = SecureQueryResult.model_validate(response.json())
        assert result.mock is False and str(result.request_id) == request_id
        assert (result.decision, result.execution_status) == ("allowed", "not_executed")
        assert result.reason.code == "QUERY_EXECUTION_DISABLED"
        health = client.get("/api/v1/health").json()
        assert health["modes"] == {"a": "mock", "c": "mock", "d": "real"}
        assert "registered_security_provider_unverified" in health["limitations"]
        assert client.post("/api/v1/auth/login", json={"username": "eve"}).status_code == 200
        assert client.get(f"/api/v1/projects/{project}/papers").status_code == 404
