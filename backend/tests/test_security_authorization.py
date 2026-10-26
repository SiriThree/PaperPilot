"""D authorization boundaries using isolated data; no database-role or RLS claims."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.mock_data import MockData, seed_id
from app.adapters.ports import Context
from app.config import Settings
from app.errors import AppError
from app.main import create_app
from app.security import build_security

ACTIONS = ("project.read", "members.manage", "paper.remove", "audit.read")


def context(user="alice", project="project-a"):
    return Context(seed_id(user), seed_id(project), uuid4())


@pytest.fixture
def data():
    return MockData()


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize(
    "user,project",
    [("alice", "project-b"), ("eve", "project-a"), ("unknown", "project-a"), ("alice", "unknown")],
)
def test_nonmember_cannot_authorize_by_claiming_owner(data, action, user, project):
    with pytest.raises(AppError) as error:
        build_security(data).authorize(context(user, project), action, "owner")
    assert (error.value.status, error.value.code) == (404, "RESOURCE_NOT_FOUND")


@pytest.mark.parametrize("action", ACTIONS)
def test_same_adapter_rechecks_revoked_membership(data, action):
    security = build_security(data)
    ctx = context("bob")
    assert security.authorize(ctx, "project.read", "member") is None
    data.remove_member(context(), ctx.user_id)
    with pytest.raises(AppError) as error:
        security.authorize(ctx, action, "member")
    assert (error.value.status, error.value.code) == (404, "RESOURCE_NOT_FOUND")


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("user,claimed_role", [("bob", "owner"), ("alice", "member")])
def test_role_snapshot_must_match_current_project(data, action, user, claimed_role):
    with pytest.raises(AppError) as error:
        build_security(data).authorize(context(user), action, claimed_role)
    assert (error.value.status, error.value.code) == (403, "ACTION_FORBIDDEN")


def test_roles_are_project_specific_for_a_user_in_both_projects(data):
    ctx = context()
    data.add_member(context("eve", "project-b"), "alice")
    security = build_security(data)
    assert security.authorize(ctx, "audit.read", "owner") is None
    other = replace(ctx, project_id=seed_id("project-b"))
    assert security.authorize(other, "project.read", "member") is None
    with pytest.raises(AppError) as error:
        security.authorize(other, "audit.read", "owner")
    assert error.value.status == 403
    with pytest.raises(AppError) as error:
        security.authorize(other, "audit.read", "member")
    assert (error.value.status, error.value.code) == (403, "OWNER_REQUIRED")


@pytest.mark.parametrize("field", ["user_id", "project_id", "request_id"])
@pytest.mark.parametrize("operation", ["authorize", "execute"])
def test_context_ids_must_be_uuid_objects(data, field, operation):
    ctx = context()
    ctx = replace(ctx, **{field: str(getattr(ctx, field))})
    security = build_security(data)
    with pytest.raises(AppError) as error:
        if operation == "authorize":
            security.authorize(ctx, "project.read", "owner")
        else:
            asyncio.run(security.execute(ctx, "按年份统计论文数量"))
    assert (error.value.status, error.value.code) == (404, "RESOURCE_NOT_FOUND")


@pytest.mark.parametrize("ctx", [None, {"user_id": "alice", "project_id": "project-a"}])
def test_context_json_is_not_an_internal_context(data, ctx):
    with pytest.raises(AppError) as error:
        build_security(data).authorize(ctx, "project.read", "owner")
    assert (error.value.status, error.value.code) == (404, "RESOURCE_NOT_FOUND")


@pytest.mark.parametrize("role", [None, "admin", "OWNER", "", {}])
def test_invalid_authoritative_role_is_denied(data, monkeypatch, role):
    monkeypatch.setattr(data, "role", lambda *args: role)
    with pytest.raises(AppError) as error:
        build_security(data).authorize(context(), "project.read", "owner")
    assert (error.value.status, error.value.code) == (404, "RESOURCE_NOT_FOUND")


@pytest.mark.parametrize("action", ["", "project.write", "project.*", "PROJECT.READ", None, []])
def test_actions_are_exact_allowlisted_names(data, action):
    with pytest.raises(AppError) as error:
        build_security(data).authorize(context(), action, "owner")
    assert (error.value.status, error.value.code) == (403, "ACTION_FORBIDDEN")


@pytest.mark.parametrize(
    "failure", [RuntimeError("PRIVATE_DATABASE_CONFIG"), AppError(500, "PRIVATE", "secret")]
)
def test_authorization_lookup_failure_is_closed_and_sanitized(data, monkeypatch, failure):
    def unavailable(*args):
        raise failure

    monkeypatch.setattr(data, "role", unavailable)
    with pytest.raises(AppError) as error:
        build_security(data).authorize(context(), "audit.read", "owner")
    assert (error.value.status, error.value.code) == (503, "GATEWAY_UNAVAILABLE")
    assert "PRIVATE" not in error.value.message and "secret" not in error.value.message


@pytest.fixture
def d_app(tmp_path):
    return create_app(
        Settings(environment="test", d_mode="real", storage_dir=tmp_path / "uploads"),
        security_factory=build_security,
    )


@pytest.mark.parametrize("user", ["alice", "bob"])
def test_http_permission_matrix_and_revocation(d_app, user):
    path = f"/api/v1/projects/{seed_id('project-a')}"
    with TestClient(d_app, headers={"X-PaperPilot-Client": "web"}) as client:
        assert client.post("/api/v1/auth/login", json={"username": user}).status_code == 200
        assert client.get(path + "/papers").status_code == 200
        expected = 200 if user == "alice" else 403
        assert client.get(path + "/members").status_code == expected
        assert client.get(path + "/audit-events").status_code == expected
        response = client.delete(path + f"/papers/{seed_id('paper-a')}?confirm_source_impact=true")
        assert response.status_code == expected
        if user == "bob":
            d_app.state.adapters.data.remove_member(context(), seed_id("bob"))
            assert client.get(path + "/papers").status_code == 404


@pytest.mark.parametrize("change", ["revoke", "downgrade", "unavailable"])
def test_http_rechecks_after_business_precheck(d_app, monkeypatch, change):
    data = d_app.state.adapters.data
    original = data.role
    calls = 0

    def changing_role(user_id, project_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == "unavailable":
                raise RuntimeError("PRIVATE_DATABASE_CONFIG")
            if change == "revoke":
                data.memberships.pop((project_id, user_id))
            else:
                data.memberships[(project_id, user_id)] = "member"
        return original(user_id, project_id)

    with TestClient(d_app, headers={"X-PaperPilot-Client": "web"}) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        monkeypatch.setattr(data, "role", changing_role)
        rid = str(uuid4())
        response = client.get(
            f"/api/v1/projects/{seed_id('project-a')}/members", headers={"X-Request-ID": rid}
        )
        status, code = {
            "revoke": (404, "RESOURCE_NOT_FOUND"),
            "downgrade": (403, "ACTION_FORBIDDEN"),
            "unavailable": (503, "GATEWAY_UNAVAILABLE"),
        }[change]
        assert response.status_code == status and response.json()["code"] == code
        assert response.json()["request_id"] == rid and response.headers["X-Request-ID"] == rid
        assert "PRIVATE_DATABASE_CONFIG" not in response.text
        assert "items" not in response.json()
