"""Real PostgreSQL + D policy mode; no restricted executor or SQL execution."""

import pytest
import psycopg
from fastapi.testclient import TestClient
from uuid import uuid4

from app.config import Settings
from app.dev_composition import create_app
from test_phase5a_persistent import WEB, database as isolated_database  # noqa: F401

pytestmark = pytest.mark.real_database


@pytest.fixture
def policy_app(isolated_database):  # noqa: F811
    _, storage = isolated_database
    return create_app(
        Settings(environment="test", a_mode="persistent", c_mode="mock", d_mode="real", storage_dir=storage)
    )


def login(client, username):
    assert client.post("/api/v1/auth/login", json={"username": username}).status_code == 200


def project(client):
    response = client.post("/api/v1/projects", json={"name": "Phase 5D policy"})
    assert response.status_code == 201
    return "/api/v1/projects/" + response.json()["project_id"]


def test_policy_disabled_query_and_audits_survive_app_recreation(policy_app, isolated_database):  # noqa: F811
    with TestClient(policy_app, headers=WEB) as client:
        login(client, "alice")
        base = project(client)
        response = client.post(base + "/secure-queries", json={"question": "按年份统计论文数量"})
        assert response.status_code == 200
        result = response.json()
        assert (result["decision"], result["execution_status"], result["mock"]) == (
            "allowed",
            "not_executed",
            False,
        )
        assert result["reason"]["code"] == "QUERY_EXECUTION_DISABLED" and result["rows"] == []
        rejected = client.post(base + "/secure-queries", json={"question": "unsupported question"}).json()
        assert (rejected["decision"], rejected["execution_status"]) == ("rejected", "not_executed")
        assert rejected["reason"]["code"] == "UNSUPPORTED_QUERY" and rejected["rows"] == []
        request_ids = {result["request_id"], rejected["request_id"]}
        events = client.get(base + "/audit-events").json()["items"]
        assert all(
            any(e["request_id"] == rid and e["action"] == "security.query.finished" for e in events)
            for rid in request_ids
        )
    _, storage = isolated_database
    app = create_app(Settings(environment="test", a_mode="persistent", d_mode="real", storage_dir=storage))
    with TestClient(app, headers=WEB) as client:
        login(client, "alice")
        events = client.get(base + "/audit-events").json()["items"]
        assert all(
            any(e["request_id"] == rid and e["action"] == "security.query.finished" for e in events)
            for rid in request_ids
        )


def test_project_authorization_revocation_and_gateway_failure(policy_app, monkeypatch):
    with TestClient(policy_app, headers=WEB) as client:
        base = project_after_login(client)
        member = client.post(base + "/members", json={"username": "bob"}).json()["user"]["user_id"]
        login(client, "bob")
        allowed = client.post(base + "/secure-queries", json={"question": "按年份统计论文数量"})
        assert allowed.status_code == 200 and allowed.json()["execution_status"] == "not_executed"
        login(client, "alice")
        assert client.delete(base + "/members/" + member).status_code == 200
        login(client, "bob")
        assert (
            client.post(base + "/secure-queries", json={"question": "按年份统计论文数量"}).status_code == 404
        )
        login(client, "alice")

        async def unavailable(*args):
            raise RuntimeError("provider fixture")

        def forbidden_fallback(*args, **kwargs):
            pytest.fail("gateway failure must never invoke direct statistics")

        monkeypatch.setattr(policy_app.state.adapters.data, "controlled_statistics", forbidden_fallback)
        monkeypatch.setattr(policy_app.state.adapters.security, "_candidate_generator", unavailable)
        result = client.post(base + "/secure-queries", json={"question": "按年份统计论文数量"})
        assert result.status_code == 503 and result.json()["code"] == "GATEWAY_UNAVAILABLE"
        assert "provider fixture" not in result.text


def project_after_login(client):
    login(client, "alice")
    return project(client)


def test_critical_audit_failure_returns_no_success(policy_app, monkeypatch):
    with TestClient(policy_app, headers=WEB) as client:
        base = project_after_login(client)
        data = policy_app.state.adapters.data
        original = data.append_audit

        def fail_finished(event):
            if event.action == "security.query.finished":
                raise RuntimeError("audit fixture")
            return original(event)

        monkeypatch.setattr(data, "append_audit", fail_finished)
        response = client.post(base + "/secure-queries", json={"question": "按年份统计论文数量"})
        assert response.status_code == 503 and response.json()["code"] == "AUDIT_UNAVAILABLE"
        assert "audit fixture" not in response.text and "rows" not in response.json()


def test_early_denials_persist_without_project_disclosure(policy_app, isolated_database):  # noqa: F811
    config, storage = isolated_database
    anonymous_id, denied_id, missing_id = [uuid4() for _ in range(3)]
    with TestClient(policy_app, headers=WEB) as client:
        login(client, "alice")
        base = project(client)
        member = client.post(base + "/members", json={"username": "bob"}).json()["user"]["user_id"]
        client.post("/api/v1/auth/logout")
        anon = client.post(
            base + "/secure-queries",
            json={"question": "private question"},
            headers={"X-Request-ID": str(anonymous_id)},
        )
        assert anon.status_code == 401
        login(client, "alice")
        assert client.delete(base + "/members/" + member).status_code == 200
        login(client, "bob")
        denied = client.post(
            base + "/secure-queries",
            json={"question": "private question"},
            headers={"X-Request-ID": str(denied_id)},
        )
        missing = client.post(
            f"/api/v1/projects/{uuid4()}/secure-queries",
            json={"question": "private question"},
            headers={"X-Request-ID": str(missing_id)},
        )
        assert denied.status_code == missing.status_code == 404
        assert denied.json()["code"] == missing.json()["code"] == "RESOURCE_NOT_FOUND"
    restarted = create_app(
        Settings(environment="test", a_mode="persistent", d_mode="real", storage_dir=storage)
    )
    with TestClient(restarted, headers=WEB) as client:
        assert client.get("/api/v1/health").status_code == 200
        with psycopg.connect(**config.conninfo()) as connection:
            rows = connection.execute(
                "SELECT request_id, actor_id, project_id, decision, execution_status, mock "
                "FROM audit_events WHERE action=%s AND request_id IN (%s,%s,%s)",
                ("secure_query.access_denied", anonymous_id, denied_id, missing_id),
            ).fetchall()
    by_id = {row[0]: row for row in rows}
    assert set(by_id) == {anonymous_id, denied_id, missing_id}
    assert by_id[anonymous_id][1] is None and by_id[denied_id][1] == by_id[missing_id][1]
    assert by_id[denied_id][1] is not None
    assert all(row[2:] == (None, "rejected", "not_executed", False) for row in rows)


def test_early_denial_audit_failure_never_executes(policy_app, monkeypatch):
    def fail(event):
        raise RuntimeError("private audit fixture")

    async def forbidden(*args):
        pytest.fail("query executed without confirmed audit")

    monkeypatch.setattr(policy_app.state.adapters.data, "append_audit", fail)
    monkeypatch.setattr(policy_app.state.adapters.security, "execute", forbidden)
    with TestClient(policy_app, headers=WEB) as client:
        response = client.post(f"/api/v1/projects/{uuid4()}/secure-queries", json={"question": "query"})
    assert response.status_code == 503 and response.json()["code"] == "AUDIT_UNAVAILABLE"
    assert "private audit fixture" not in response.text
