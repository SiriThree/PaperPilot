"""Composition tests with in-memory doubles, never persistence acceptance evidence."""

import asyncio
import inspect
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.data_binding import DataBinding
from app.adapters.factory import create_adapters
from app.adapters.mock_data import MockData, seed_id
from app.adapters.ports import Context, DataPort
from app.config import Settings
from app.errors import AppError
from app.main import create_app
from app.schemas import QuestionRequest


class ContractDouble:
    """Only a delegation double to verify B's injection; it has no database attribute."""

    def __init__(self):
        self.delegate = MockData()

    def __getattr__(self, name):
        return getattr(self.delegate, name)


@pytest.mark.parametrize("mode", ["persistent", "real"])
def test_unregistered_data_mode_fails_without_creating_files(tmp_path, mode):
    with pytest.raises(RuntimeError, match="not registered"):
        create_app(Settings(environment="test", a_mode=mode, storage_dir=tmp_path / "uploads"))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mode", ["persistent", "real"])
def test_explicit_binding_uses_port_and_neutral_readiness(mode):
    calls = []
    data = ContractDouble()
    binding = DataBinding(data, lambda: calls.append("ready"), lambda: calls.append("close"))
    app = create_app(Settings(environment="test", a_mode=mode), data_binding=binding)
    assert app.state.adapters.data is data
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["modes"]["a"] == mode
    assert calls == ["ready", "ready", "close"]


@pytest.mark.parametrize("failure", ["raise", "false"])
def test_binding_failure_is_safe_and_never_falls_back(failure):
    def not_ready():
        if failure == "raise":
            raise RuntimeError("PRIVATE_PROVIDER_DETAIL")
        return False

    with pytest.raises(RuntimeError) as error:
        create_adapters(Settings(a_mode="persistent"), data_binding=DataBinding(ContractDouble(), not_ready))
    assert "readiness failed" in str(error.value) and "PRIVATE_PROVIDER_DETAIL" not in str(error.value)


def test_unhealthy_readiness_returns_safe_unavailable():
    state = {"healthy": True}
    binding = DataBinding(ContractDouble(), lambda: state["healthy"])
    app = create_app(Settings(environment="test", a_mode="persistent"), data_binding=binding)
    state["healthy"] = False
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 503 and response.json()["code"] == "DATA_UNAVAILABLE"


def test_mock_cannot_masquerade_as_persistence_or_override_explicit_mock():
    with pytest.raises(RuntimeError, match="Invalid persistent"):
        create_adapters(Settings(a_mode="persistent"), data_binding=DataBinding(MockData(), lambda: None))
    with pytest.raises(RuntimeError, match="Explicit Mock"):
        create_adapters(Settings(), data_binding=DataBinding(ContractDouble(), lambda: None))


def test_incomplete_data_binding_is_rejected():
    with pytest.raises(RuntimeError, match="Invalid persistent"):
        create_adapters(Settings(a_mode="persistent"), data_binding=DataBinding(object(), lambda: None))


def test_mock_parameter_signatures_match_26_frozen_methods():
    methods = [name for name in vars(DataPort) if not name.startswith("_")]
    assert len(methods) == 26
    for name in methods:
        port = inspect.signature(getattr(DataPort, name))
        mock = inspect.signature(getattr(MockData, name))
        shape = lambda s: [(p.name, p.kind, p.default) for p in s.parameters.values()]  # noqa: E731
        assert shape(port) == shape(mock), name


@pytest.mark.parametrize("error_kind", ["known_unknown", "opaque_exception"])
def test_unacknowledged_task_completion_is_not_marked_failed(app, monkeypatch, error_kind):
    data = app.state.adapters.data
    ctx = Context(seed_id("alice"), seed_id("project-a"), uuid4())
    data.create_task(
        ctx,
        "answer",
        {"question": QuestionRequest(question="测试"), "paper_ids": [seed_id("paper-a")]},
        "completion-unknown",
    )
    job = data.claim_job()
    complete = data.complete_job
    failures = []

    def commit_without_ack(*args):
        complete(*args)
        if error_kind == "known_unknown":
            raise AppError(503, "DATA_COMMIT_UNKNOWN", "unknown")
        raise RuntimeError("PRIVATE_COMMIT_DETAIL")

    monkeypatch.setattr(data, "complete_job", commit_without_ack)
    monkeypatch.setattr(data, "fail_job", lambda *args: failures.append(args))
    asyncio.run(app.state.service.run_job(job))
    assert not failures and data.task(ctx, job.task.task_id).status == "succeeded"
