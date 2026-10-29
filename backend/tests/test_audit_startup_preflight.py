"""Application wiring tests: doubles here do not establish database security."""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.adapters.data_binding import DataBinding
from app.adapters.mock_data import MockData
from app.config import Settings
from app.main import create_app


class DataDouble:
    def __init__(self):
        self.data = MockData()
        self.database = DatabaseDouble()

    def __getattr__(self, name):
        return getattr(self.data, name)


class DatabaseDouble:
    @contextmanager
    def transaction(self):
        yield self


def configured(**values):
    return Settings(
        environment="test",
        a_mode="persistent",
        audit_preflight="required",
        audit_trusted_owner="reviewed_owner",
        **values,
    )


def test_required_checks_binding_at_startup_and_every_health_request(monkeypatch):
    from app.data import audit_boundary

    calls = []
    healthy = {"value": True}

    def verify(connection, *, trusted_owner):
        calls.append((connection, trusted_owner))
        if not healthy["value"]:
            raise PermissionError("sensitive driver detail")

    monkeypatch.setattr(audit_boundary, "verify_audit_boundary", verify)
    closed = []
    data = DataDouble()
    app = create_app(configured(), data_binding=DataBinding(data, lambda: True, lambda: closed.append(1)))
    assert calls == [(data.database, "reviewed_owner")]
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert "audit_preflight_required" in response.json()["limitations"]
        healthy["value"] = False
        response = client.get("/api/v1/health")
        assert response.status_code == 503
        assert "sensitive driver detail" not in response.text
    assert len(calls) == 3
    assert closed == [1]


def test_required_rejects_missing_configuration_and_non_database_binding():
    with pytest.raises(RuntimeError, match="independently trusted owner"):
        create_app(Settings(environment="test", a_mode="persistent", audit_preflight="required"))
    with pytest.raises(RuntimeError, match="PostgreSQL database binding"):
        create_app(configured(), data_binding=DataBinding(object(), lambda: True))


def test_data_entry_closes_binding_when_required_preflight_fails(monkeypatch):
    from app.data import audit_boundary, binding
    from app.data.database import ConnectionConfig

    closed = []
    monkeypatch.setattr(ConnectionConfig, "from_env", lambda: object())
    monkeypatch.setattr(
        binding,
        "compose_data_binding",
        lambda *_: DataBinding(DataDouble(), lambda: True, lambda: closed.append(1)),
    )

    def reject(*args, **kwargs):
        raise PermissionError("private provider detail")

    monkeypatch.setattr(audit_boundary, "verify_audit_boundary", reject)
    with pytest.raises(RuntimeError, match="readiness failed") as error:
        binding.create_app(configured())
    assert "private provider detail" not in str(error.value)
    assert closed == [1]


def test_failed_preflight_refuses_startup_without_exposing_exception(monkeypatch):
    from app.data import audit_boundary

    def reject(*args, **kwargs):
        raise PermissionError("confidential diagnostic")

    monkeypatch.setattr(audit_boundary, "verify_audit_boundary", reject)
    with pytest.raises(RuntimeError, match="readiness failed") as error:
        create_app(configured(), data_binding=DataBinding(DataDouble(), lambda: True))
    assert "confidential diagnostic" not in str(error.value)


def test_disabled_is_reported_and_remains_available_for_mock_test():
    app = create_app(Settings(environment="test"))
    with TestClient(app) as client:
        assert "audit_preflight_disabled" in client.get("/api/v1/health").json()["limitations"]


def test_environment_config_is_explicit(monkeypatch):
    monkeypatch.setenv("PAPERPILOT_AUTH_MODE", "development")
    monkeypatch.setenv("PAPERPILOT_A_MODE", "persistent")
    monkeypatch.setenv("PAPERPILOT_AUDIT_PREFLIGHT", "required")
    monkeypatch.setenv("PAPERPILOT_AUDIT_TRUSTED_OWNER", "reviewed_owner")
    settings = Settings.from_env()
    settings.validate()
    assert settings.audit_preflight == "required"
    assert settings.audit_trusted_owner == "reviewed_owner"


def test_runtime_preflight_failure_stops_gateway_without_leaking_detail(monkeypatch):
    from app.data import audit_boundary

    healthy = {"value": True}
    calls = []

    def verify(connection, *, trusted_owner):
        if not healthy["value"]:
            raise PermissionError("private privilege detail")

    monkeypatch.setattr(audit_boundary, "verify_audit_boundary", verify)
    app = create_app(configured(), data_binding=DataBinding(DataDouble(), lambda: True))

    async def must_not_run(*args):
        calls.append(True)
        raise AssertionError("gateway must not be called")

    monkeypatch.setattr(app.state.adapters.security, "execute", must_not_run)
    with TestClient(app, headers={"X-PaperPilot-Client": "web"}) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        project = client.post("/api/v1/projects", json={"name": "preflight fixture"}).json()["project_id"]
        healthy["value"] = False
        response = client.post(
            f"/api/v1/projects/{project}/secure-queries", json={"question": "按年份统计论文数量"}
        )
        assert response.status_code == 503
        assert response.json()["code"] == "AUDIT_UNAVAILABLE"
        assert "private privilege detail" not in response.text
        assert calls == []
