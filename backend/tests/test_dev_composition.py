"""Composition tests use a data double; they do not certify persistence or AI."""

import pytest
from fastapi.testclient import TestClient

from app.adapters.data_binding import DataBinding
from app.adapters.mock_data import MockData
from app.config import Settings
from app.dev_composition import create_app


class DataDouble:
    def __init__(self):
        self.data = MockData()

    def __getattr__(self, name):
        return getattr(self.data, name)


@pytest.fixture
def configuration(monkeypatch):
    from app.data import binding
    from app.data.database import ConnectionConfig

    events = []
    monkeypatch.setattr(ConnectionConfig, "from_env", lambda: object())
    monkeypatch.setattr(
        binding,
        "compose_data_binding",
        lambda *_: DataBinding(DataDouble(), lambda: True, lambda: events.append("data")),
    )
    for key, value in {
        "PAPERPILOT_AI_BASE_URL": "http://127.0.0.1:9",
        "PAPERPILOT_AI_API_KEY": "test-only",
        "PAPERPILOT_AI_CHAT_MODEL": "fixture",
        "PAPERPILOT_AI_EMBEDDING_MODEL": "fixture",
        "PAPERPILOT_AI_EMBEDDING_DIMENSIONS": "3",
    }.items():
        monkeypatch.setenv(key, value)
    return events


@pytest.mark.parametrize(
    "a,c,d",
    [
        ("mock", "mock", "mock"),
        ("persistent", "mock", "mock"),
        ("persistent", "real", "mock"),
        ("persistent", "real", "real"),
    ],
)
def test_modes_are_explicit(configuration, a, c, d, tmp_path):
    app = create_app(Settings(environment="test", a_mode=a, c_mode=c, d_mode=d, storage_dir=tmp_path))
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["modes"] == {"a": a, "c": c, "d": d}
        if d == "real":
            assert app.state.adapters.security._query_executor is None
    assert configuration == (["data"] if a != "mock" else [])


def test_scoped_business_never_enabled_implicitly(monkeypatch):
    monkeypatch.setenv("PAPERPILOT_SCOPED_BUSINESS_BINDINGS_FILE", "fixture")
    with pytest.raises(RuntimeError, match="isolated test mode"):
        create_app(Settings(environment="test", a_mode="mock"))
    with pytest.raises(RuntimeError):
        create_app(Settings(environment="production", a_mode="mock"))


def test_cleanup_reverse_order_preserves_original_failure(configuration, monkeypatch, tmp_path):
    from app.ai.adapter import RealAI
    from app import security

    monkeypatch.setattr(RealAI, "close", lambda self: configuration.append("ai"), raising=False)

    def fail(_):
        raise RuntimeError("private security initialization detail")

    monkeypatch.setattr(security, "build_security", fail)
    with pytest.raises(RuntimeError, match="D adapter initialization failed") as error:
        create_app(
            Settings(
                environment="test", a_mode="persistent", c_mode="real", d_mode="real", storage_dir=tmp_path
            )
        )
    assert "private security" not in str(error.value)
    assert configuration == ["ai", "data"]


def test_configuration_failure_closes_data_without_masking_error(monkeypatch):
    from app.data import binding
    from app.data.database import ConnectionConfig

    calls = []

    def close():
        calls.append("data")
        raise RuntimeError("private cleanup")

    monkeypatch.setattr(ConnectionConfig, "from_env", lambda: object())
    monkeypatch.setattr(
        binding, "compose_data_binding", lambda *_: DataBinding(DataDouble(), lambda: True, close)
    )
    monkeypatch.delenv("PAPERPILOT_AI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="Invalid or missing PAPERPILOT_AI configuration"):
        create_app(Settings(environment="test", a_mode="persistent", c_mode="real"))
    assert calls == ["data"]


def test_failed_readiness_closes_binding_and_never_falls_back(monkeypatch):
    from app.data import binding
    from app.data.database import ConnectionConfig

    calls = []
    monkeypatch.setattr(ConnectionConfig, "from_env", lambda: object())
    monkeypatch.setattr(
        binding,
        "compose_data_binding",
        lambda *_: DataBinding(DataDouble(), lambda: False, lambda: calls.append("close")),
    )
    with pytest.raises(RuntimeError, match="readiness failed"):
        create_app(Settings(environment="test", a_mode="persistent"))
    assert calls == ["close"]


def test_ai_deadline_must_fit_data_lease(configuration, monkeypatch):
    monkeypatch.setenv("PAPERPILOT_LEASE_SECONDS", "1")
    with pytest.raises(RuntimeError, match="shorter than the task lease"):
        create_app(Settings(environment="test", a_mode="persistent", c_mode="real"))
    assert configuration == ["data"]
