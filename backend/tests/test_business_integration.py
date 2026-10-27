"""B composition/failure isolation with port doubles, never real AI/SQL certification."""

import pytest
from fastapi.testclient import TestClient

from app.adapters.mock_ai import MockAI
from app.adapters.mock_security import MockSecurity
from app.config import Settings
from app.errors import AppError
from app.main import create_app
from app.schemas import SecureQueryResult


class UnavailableAI:
    def __init__(self, data):
        self.delegate = MockAI(data)

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def search(self, ctx, query):
        raise AppError(503, "AI_UNAVAILABLE", "AI 服务暂不可用")


def test_registered_ai_failure_keeps_basic_reads_and_reports_available(tmp_path):
    app = create_app(
        Settings(environment="test", c_mode="real", storage_dir=tmp_path), ai_factory=UnavailableAI
    )
    with TestClient(app, headers={"X-PaperPilot-Client": "web"}) as client:
        assert client.get("/api/v1/projects").status_code == 401
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        projects = client.get("/api/v1/projects").json()["items"]
        base = f"/api/v1/projects/{projects[0]['project_id']}"
        version = client.post(
            base + "/reports",
            json={"title": "隔离报告", "research_question": "问题", "body": "已保存内容", "sources": []},
        )
        assert version.status_code == 201
        result = client.post(base + "/search", json={"query": "问题"})
        assert result.status_code == 503 and result.json()["code"] == "AI_UNAVAILABLE"
        assert result.json()["request_id"] == result.headers["X-Request-ID"]
        assert client.get(base + "/papers").status_code == 200
        assert client.get(base + "/reports").json()["items"][0]["body"] == "已保存内容"
        report = version.json()["report_id"]
        assert client.get(base + f"/reports/{report}/versions").json()["items"][0] == version.json()
        other = client.post("/api/v1/projects", json={"name": "另一项目"}).json()["project_id"]
        denied = client.get(f"/api/v1/projects/{other}/reports/{report}/versions")
        assert denied.status_code == 404 and "已保存内容" not in denied.text


@pytest.mark.parametrize("failure", ["known", "opaque", "audit_before", "audit_after"])
def test_registered_gateway_failure_never_uses_fallback_or_returns_unconfirmed_rows(tmp_path, failure):
    calls = []

    class Gateway:
        def __init__(self, data):
            self.delegate = MockSecurity(data)

        def authorize(self, ctx, action, role):
            return self.delegate.authorize(ctx, action, role)

        async def execute(self, ctx, question):
            calls.append(ctx)
            if failure == "known":
                raise AppError(503, "GATEWAY_UNAVAILABLE", "安全网关不可用")
            if failure == "opaque":
                raise RuntimeError("PRIVATE_EXECUTOR_DETAIL")
            return SecureQueryResult(
                decision="allowed",
                execution_status="executed",
                columns=["count"],
                rows=[{"count": 1}],
                request_id=ctx.request_id,
                policy_id="test-double-only",
                mock=True,
            )

    app = create_app(
        Settings(environment="test", d_mode="real", storage_dir=tmp_path), security_factory=Gateway
    )
    with TestClient(app, headers={"X-PaperPilot-Client": "web"}) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        project = client.get("/api/v1/projects").json()["items"][0]["project_id"]
        data = app.state.adapters.data
        fallback_calls = []

        def forbidden(*args):
            fallback_calls.append(args)
            raise AssertionError("Business code must not call statistics as a gateway fallback")

        data.controlled_statistics = forbidden
        append = data.append_audit

        def audit(event):
            if (failure == "audit_before" and event.action == "secure_query.requested") or (
                failure == "audit_after" and event.action == "secure_query.finished"
            ):
                raise RuntimeError("PRIVATE_AUDIT_DETAIL")
            append(event)

        data.append_audit = audit
        response = client.post(f"/api/v1/projects/{project}/secure-queries", json={"question": "统计"})
        assert response.status_code == 503
        assert response.json()["code"] == (
            "AUDIT_UNAVAILABLE" if failure.startswith("audit_") else "GATEWAY_UNAVAILABLE"
        )
        assert "rows" not in response.json() and "PRIVATE_" not in response.text
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
        assert len(calls) == (0 if failure == "audit_before" else 1)
        assert not fallback_calls
        assert client.get(f"/api/v1/projects/{project}/papers").status_code == 200


def test_remove_paper_is_authorized_even_with_explicit_impact_confirmation(app, signed, project):
    paper = signed.get(f"/api/v1/projects/{project}/papers").json()["items"][0]
    path = f"/api/v1/projects/{project}/papers/{paper['paper_id']}?confirm_source_impact=true"
    anonymous = TestClient(app, headers={"X-PaperPilot-Client": "web"})
    member = TestClient(app, headers={"X-PaperPilot-Client": "web"})
    try:
        assert anonymous.delete(path).status_code == 401
        assert member.post("/api/v1/auth/login", json={"username": "bob"}).status_code == 200
        denied = member.delete(path)
        assert denied.status_code == 403 and paper["title"] not in denied.text
        assert signed.get(path.split("?")[0]).status_code == 200
    finally:
        anonymous.close()
        member.close()
