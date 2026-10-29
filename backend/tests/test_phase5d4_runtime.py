import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.adapters.data_binding import DataBinding
from app.adapters.mock_data import MockData
from app.main import create_app
from app.schemas import SecureQueryResult

WEB = {"X-PaperPilot-Client": "web"}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Proactor compatibility probe")
def test_psycopg_default_windows_proactor_rejects_async_connection():
    code = (
        "import asyncio, psycopg\n"
        "async def probe():\n"
        " try: await psycopg.AsyncConnection.connect(host='127.0.0.1', port=1, connect_timeout=1)\n"
        " except psycopg.InterfaceError as exc:\n"
        "  assert 'ProactorEventLoop' in str(exc); return\n"
        " raise AssertionError('expected driver incompatibility')\n"
        "asyncio.run(probe())\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=10)
    assert result.returncode == 0


class ExecutorFixture:
    def __init__(self, fail=False):
        self._ready = False
        self._closed = False
        self.fail = fail

    async def ready(self):
        if self.fail:
            raise RuntimeError("readiness failed")
        self._ready = True
        return True

    def close(self):
        self._closed = True
        self._ready = False


def test_executor_lifecycle_and_unhealthy_runtime():
    app = create_app(Settings(environment="test"))
    executor = ExecutorFixture()
    app.state.restricted_executor = executor
    with TestClient(app) as client:
        assert "security_executor_ready" in client.get("/api/v1/health").json()["limitations"]
        executor._ready = False
        assert client.get("/api/v1/health").status_code == 503
    assert executor._closed


def test_readiness_failure_closes_and_preserves_error():
    app = create_app(Settings(environment="test"))
    executor = ExecutorFixture(fail=True)
    app.state.restricted_executor = executor
    with pytest.raises(RuntimeError, match="readiness failed"), TestClient(app):
        pass
    assert executor._closed


def test_startup_failure_cleanup_order_and_original_error():
    events = []

    class BrokenExecutor(ExecutorFixture):
        def close(self):
            events.append("executor")
            raise RuntimeError("cleanup error")

    class DataDouble:
        def __init__(self):
            self.data = MockData()

        def __getattr__(self, name):
            return getattr(self.data, name)

    binding = DataBinding(DataDouble(), lambda: True, lambda: events.append("data"))
    app = create_app(Settings(environment="test", a_mode="persistent"), data_binding=binding)
    app.state.restricted_executor = BrokenExecutor(fail=True)
    with pytest.raises(RuntimeError, match="readiness failed"), TestClient(app):
        pass
    assert events == ["executor", "data"]


def test_candidate_mode_is_explicit_and_test_only():
    with pytest.raises(RuntimeError):
        Settings(security_execution="candidate").validate()
    with pytest.raises(RuntimeError):
        Settings(
            environment="test", a_mode="persistent", d_mode="mock", security_execution="candidate"
        ).validate()
    Settings(
        environment="test", a_mode="persistent", d_mode="real", security_execution="candidate"
    ).validate()
    with pytest.raises(RuntimeError, match="registration"):
        create_app(
            Settings(environment="test", a_mode="persistent", d_mode="real", security_execution="candidate")
        )


def test_missing_restricted_configuration_fails_closed(monkeypatch):
    from app.security.restricted_executor import RestrictedConnection

    monkeypatch.delenv("PAPERPILOT_SECURITY_ISOLATED_TEST", raising=False)
    with pytest.raises(RuntimeError, match="isolated"):
        RestrictedConnection.from_env()


@pytest.mark.parametrize("size, expected", [(260000, 200), (262144, 503)])
def test_complete_serialized_http_response_budget(size, expected, monkeypatch):
    app = create_app(Settings(environment="test"))

    async def result(ctx, question):
        return SecureQueryResult(
            decision="allowed",
            execution_status="executed",
            columns=["value"],
            rows=[{"value": "x" * size}],
            policy_id="fixture",
            request_id=ctx.request_id,
            mock=False,
        )

    monkeypatch.setattr(app.state.service, "secure_query", result)
    with TestClient(app, headers=WEB) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        project = client.post("/api/v1/projects", json={"name": "response budget"}).json()["project_id"]
        response = client.post(f"/api/v1/projects/{project}/secure-queries", json={"question": "test"})
        assert response.status_code == expected
        if expected == 503:
            assert response.json()["code"] == "QUERY_RESPONSE_TOO_LARGE"
            assert "rows" not in response.json()
        else:
            assert len(response.content) <= 256 * 1024
