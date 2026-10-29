"""Credential HTTP integration against the explicitly isolated PostgreSQL fixture."""

import secrets
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.data_binding import DataBinding
from app.config import Settings
from app.data.authentication import PostgresSessions
from app.main import create_app
from test_outbox_delivery import harness  # noqa: F401

pytestmark = pytest.mark.real_database


def test_credentials_csrf_and_session_survive_app_rebuild(harness, tmp_path):  # noqa: F811
    password = secrets.token_urlsafe(24)
    store = PostgresSessions(harness.database)
    store.initialize_password("alice", password)
    binding = DataBinding(harness.data, harness.database.ready)
    settings = Settings(
        environment="test",
        a_mode="persistent",
        auth_mode="credentials",
        storage_dir=tmp_path,
        allowed_origins=("http://trusted.test",),
    )
    origin = {"Origin": "http://trusted.test"}
    with TestClient(create_app(settings, data_binding=binding), base_url="http://trusted.test") as client:
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        headers = {**origin, "X-CSRF-Token": csrf}
        assert (
            client.post("/api/v1/auth/login", json={"username": "alice", "password": password}).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/auth/login",
                headers={**headers, "Origin": "http://evil.test"},
                json={"username": "alice", "password": password},
            ).status_code
            == 403
        )
        bad = client.post(
            "/api/v1/auth/login", headers=headers, json={"username": "alice", "password": "wrong"}
        )
        missing = client.post(
            "/api/v1/auth/login", headers=headers, json={"username": "missing", "password": "wrong"}
        )
        assert bad.status_code == missing.status_code == 401
        assert bad.json()["code"] == missing.json()["code"] == "INVALID_IDENTITY"
        assert (
            client.post("/api/v1/auth/login", headers=headers, json={"username": "alice"}).status_code == 401
        )
        login = client.post(
            "/api/v1/auth/login", headers=headers, json={"username": "alice", "password": password}
        )
        assert login.status_code == 200 and login.json()["authentication"] == "authentication_credentials"
        assert "httponly" in login.headers["set-cookie"].lower()
        token = client.cookies.get("pp_session")
        assert client.get("/api/v1/auth/me").status_code == 200
    with TestClient(create_app(settings, data_binding=binding), base_url="http://trusted.test") as client:
        client.cookies.set("pp_session", token, path="/api/v1")
        assert client.get("/api/v1/auth/me").status_code == 200
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        assert client.post("/api/v1/auth/logout", headers={**origin, "X-CSRF-Token": csrf}).status_code == 200
        client.cookies.set("pp_session", token, path="/api/v1")
        assert client.get("/api/v1/auth/me").status_code == 401
        rejected_origin = client.options(
            "/api/v1/auth/login",
            headers={"Origin": "http://evil.test", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in rejected_origin.headers
    with harness.database.transaction() as conn:
        events = conn.execute(
            "SELECT action, request_id FROM audit_events WHERE action IN ('auth.login','auth.logout')"
        ).fetchall()
        assert sum(item["action"] == "auth.login" for item in events) >= 4
        assert sum(item["action"] == "auth.logout" for item in events) == 2
        assert all(item["request_id"] for item in events)
        invalid = conn.execute(
            "SELECT decision, execution_status FROM audit_events WHERE action='auth.session.invalid' "
            "AND actor_id=(SELECT user_id FROM users WHERE username='alice')"
        ).fetchall()
        assert any(item == {"decision": "rejected", "execution_status": "completed"} for item in invalid)


def test_failed_session_creation_does_not_claim_login_completed(harness, tmp_path, monkeypatch):  # noqa: F811
    password = secrets.token_urlsafe(24)
    PostgresSessions(harness.database).initialize_password("bob", password)
    binding = DataBinding(harness.data, harness.database.ready)
    settings = Settings(
        environment="test",
        a_mode="persistent",
        auth_mode="credentials",
        storage_dir=tmp_path,
        allowed_origins=("http://trusted.test",),
    )
    app = create_app(settings, data_binding=binding)

    def creation_failed(*_args):
        raise RuntimeError("injected session storage failure")

    monkeypatch.setattr(app.state.sessions, "create", creation_failed)
    request_id = str(uuid4())
    with TestClient(app, base_url="http://trusted.test") as client:
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        response = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://trusted.test", "X-CSRF-Token": csrf, "X-Request-ID": request_id},
            json={"username": "bob", "password": password},
        )
        assert response.status_code == 500
        assert "pp_session" not in response.cookies
    with harness.database.transaction() as conn:
        event = conn.execute(
            "SELECT decision, execution_status FROM audit_events WHERE request_id=%s AND action='auth.login'",
            (request_id,),
        ).fetchone()
    assert event == {"decision": "allowed", "execution_status": "pending"}


def test_session_changes_roll_back_on_terminal_audit_failure(harness, tmp_path, monkeypatch):  # noqa: F811
    password = secrets.token_urlsafe(24)
    PostgresSessions(harness.database).initialize_password("eve", password)
    binding = DataBinding(harness.data, harness.database.ready)
    settings = Settings(
        environment="test",
        a_mode="persistent",
        auth_mode="credentials",
        storage_dir=tmp_path,
        allowed_origins=("http://trusted.test",),
    )
    app = create_app(settings, data_binding=binding)
    with TestClient(app, base_url="http://trusted.test") as client:
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        headers = {"Origin": "http://trusted.test", "X-CSRF-Token": csrf}
        assert (
            client.post(
                "/api/v1/auth/login", headers=headers, json={"username": "eve", "password": password}
            ).status_code
            == 200
        )
        old_token = client.cookies.get("pp_session")
        original = app.state.sessions._audit_completed

        def fail_audit(*_args):
            from app.errors import AppError

            raise AppError(503, "AUDIT_UNAVAILABLE", "audit unavailable")

        monkeypatch.setattr(app.state.sessions, "_audit_completed", fail_audit)
        failed_login = client.post(
            "/api/v1/auth/login", headers=headers, json={"username": "eve", "password": password}
        )
        assert failed_login.status_code == 503
        assert client.cookies.get("pp_session") == old_token
        assert client.get("/api/v1/auth/me").status_code == 200
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 503
        assert client.get("/api/v1/auth/me").status_code == 200
        with harness.database.transaction() as conn:
            active = conn.execute(
                "SELECT count(*) AS n FROM auth_sessions s JOIN users u ON u.user_id=s.user_id "
                "WHERE u.username='eve' AND s.revoked_at IS NULL"
            ).fetchone()
            assert active["n"] == 1
        monkeypatch.setattr(app.state.sessions, "_audit_completed", original)
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
        with harness.database.transaction() as conn:
            statuses = conn.execute(
                "SELECT execution_status FROM audit_events WHERE action='auth.logout' "
                "AND actor_id=(SELECT user_id FROM users WHERE username='eve') ORDER BY timestamp"
            ).fetchall()
        assert [s["execution_status"] for s in statuses] == ["pending", "pending", "completed"]


def test_credential_login_limit_uses_asgi_peer_not_forwarded_headers(harness, tmp_path, monkeypatch):  # noqa: F811
    settings = Settings(
        environment="test",
        a_mode="persistent",
        auth_mode="credentials",
        storage_dir=tmp_path,
        allowed_origins=("http://trusted.test",),
        auth_login_limit=2,
        auth_login_window_seconds=60,
    )
    app = create_app(settings, data_binding=DataBinding(harness.data, harness.database.ready))
    with TestClient(app, base_url="http://trusted.test", client=("192.0.2.17", 3456)) as client:
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        headers = {"Origin": "http://trusted.test", "X-CSRF-Token": csrf}
        for identity in ("missing-one", "missing-two"):
            assert (
                client.post(
                    "/api/v1/auth/login", headers=headers, json={"username": identity, "password": "wrong"}
                ).status_code
                == 401
            )
        limited = client.post(
            "/api/v1/auth/login",
            headers={**headers, "X-Forwarded-For": "192.0.2.18"},
            json={"username": "another", "password": "wrong"},
        )
        assert limited.status_code == 429
        assert limited.json()["code"] == "AUTH_RATE_LIMITED"
        with harness.database.transaction() as conn:
            event = conn.execute(
                "SELECT actor_id, project_id, decision, execution_status FROM audit_events "
                "WHERE action='auth.login.rate_limited' AND request_id=%s",
                (limited.headers["X-Request-ID"],),
            ).fetchone()
        assert event == {
            "actor_id": None,
            "project_id": None,
            "decision": "rejected",
            "execution_status": "completed",
        }

        def unavailable(*_args):
            from app.errors import AppError

            raise AppError(503, "AUDIT_UNAVAILABLE", "audit unavailable")

        monkeypatch.setattr(app.state.sessions, "_audit_completed", unavailable)
        failed = client.post(
            "/api/v1/auth/login", headers=headers, json={"username": "missing", "password": "wrong"}
        )
        assert failed.status_code == 503 and failed.json()["code"] == "AUDIT_UNAVAILABLE"
