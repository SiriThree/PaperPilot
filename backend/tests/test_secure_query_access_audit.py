from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import AppError
from app.main import create_app

WEB = {"X-PaperPilot-Client": "web"}


@pytest.mark.parametrize("logged_in", [False, True])
def test_early_denials_are_audited_without_project_or_question(logged_in, monkeypatch):
    app = create_app(Settings(environment="test"))
    recorded = []
    original = app.state.adapters.data.append_audit

    def record(event):
        recorded.append(event)
        original(event)

    async def forbidden(*args):
        pytest.fail("unauthorized request must not reach security execution")

    monkeypatch.setattr(app.state.adapters.data, "append_audit", record)
    monkeypatch.setattr(app.state.adapters.security, "execute", forbidden)
    request_id = str(uuid4())
    target = uuid4()
    question = "PRIVATE_QUESTION_SENTINEL"
    with TestClient(app, headers=WEB) as client:
        if logged_in:
            assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        response = client.post(
            f"/api/v1/projects/{target}/secure-queries",
            json={"question": question},
            headers={"X-Request-ID": request_id},
        )
    assert response.status_code == (404 if logged_in else 401)
    assert response.json()["request_id"] == request_id
    assert question not in response.text and str(target) not in response.text
    event = next(event for event in recorded if event.action == "secure_query.access_denied")
    assert str(event.request_id) == request_id
    assert event.project_id is None and (event.actor_id is not None) == logged_in
    assert (event.decision, event.execution_status, event.mock) == ("rejected", "not_executed", True)
    assert question not in event.model_dump_json() and str(target) not in event.model_dump_json()


def test_denied_query_audit_failure_returns_safe_error_without_execution(monkeypatch):
    app = create_app(Settings(environment="test"))

    def fail(event):
        raise RuntimeError("PRIVATE_AUDIT_DETAIL")

    async def forbidden(*args):
        pytest.fail("audit failure must not execute query")

    monkeypatch.setattr(app.state.adapters.data, "append_audit", fail)
    monkeypatch.setattr(app.state.adapters.security, "execute", forbidden)
    request_id = str(uuid4())
    with TestClient(app, headers=WEB) as client:
        response = client.post(
            f"/api/v1/projects/{uuid4()}/secure-queries",
            json={"question": "query"},
            headers={"X-Request-ID": request_id},
        )
    assert response.status_code == 503 and response.json()["code"] == "AUDIT_UNAVAILABLE"
    assert response.json()["request_id"] == request_id and "PRIVATE" not in response.text


def test_security_authorization_rejection_is_audited(monkeypatch):
    app = create_app(Settings(environment="test"))
    recorded = []
    append = app.state.adapters.data.append_audit

    def record(event):
        recorded.append(event)
        append(event)

    def reject(*args):
        raise AppError(403, "ACTION_FORBIDDEN", "拒绝访问")

    with TestClient(app, headers=WEB) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        project = client.post("/api/v1/projects", json={"name": "authorization"}).json()["project_id"]
        monkeypatch.setattr(app.state.adapters.data, "append_audit", record)
        monkeypatch.setattr(app.state.adapters.security, "authorize", reject)
        response = client.post(f"/api/v1/projects/{project}/secure-queries", json={"question": "query"})
    assert response.status_code == 403 and response.json()["code"] == "ACTION_FORBIDDEN"
    assert len(recorded) == 1 and recorded[0].action == "secure_query.access_denied"
    assert recorded[0].actor_id is not None and recorded[0].project_id is None
