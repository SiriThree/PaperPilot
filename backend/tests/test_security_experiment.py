import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import experiment


@pytest.fixture
def local_client(tmp_path):
    app = create_app(Settings(environment="test", security_experiments=True, storage_dir=tmp_path))
    with TestClient(
        app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
        headers={
            "X-PaperPilot-Client": "web",
            "Origin": "http://127.0.0.1:5173",
        },
    ) as client:
        yield client


def owner(client):
    assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
    return client.get("/api/v1/projects").json()["items"][0]["project_id"]


def test_disabled_and_production_refusal(signed, project):
    path = f"/api/v1/projects/{project}/security-experiments"
    assert signed.get(path).json()["enabled"] is False
    assert signed.post(path, json={}).json()["code"] == "EXPERIMENT_DISABLED"
    with pytest.raises(RuntimeError, match="experiments"):
        Settings(environment="production", security_experiments=True).validate()


def test_requires_current_owner_loopback_origin_and_fixed_input(local_client):
    c = local_client
    assert c.get(f"/api/v1/projects/{uuid4()}/security-experiments").status_code == 401
    project = owner(c)
    path = f"/api/v1/projects/{project}/security-experiments"
    assert c.get(path).json()["enabled"] is True
    for body in ({"sql": "DROP TABLE papers"}, {"output": "../../escape"}, {"dsn": "business"}):
        assert c.post(path, json=body).status_code == 422
    assert c.post(path, json={}, headers={"Origin": "http://evil.invalid"}).status_code == 403
    assert c.post(path, json={}, headers={"Origin": ""}).status_code == 403
    assert c.get(path, headers={"Host": "evil.invalid"}).status_code == 403
    assert c.post(f"/api/v1/projects/{project}/members", json={"username": "bob"}).status_code == 201
    c.post("/api/v1/auth/login", json={"username": "bob"})
    assert c.get(path).status_code == 403
    assert c.post(path, json={}).status_code == 403
    c.post("/api/v1/auth/login", json={"username": "eve"})
    assert c.get(path).status_code == 404


def install_child(monkeypatch, tmp_path, *, code=0, error=None, wrong_run=False, cleanup_error=False):
    processes, cleaned = [], []
    monkeypatch.setattr(experiment, "ROOT", tmp_path)

    class Process:
        def __init__(self):
            self.returncode = None
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(b"no_gateway: running fixed cases\n")
            if error:
                self.stdout.feed_data(f"EXPERIMENT_ERROR:{error}\n".encode())
            self.done = asyncio.Event()

        async def finish(self):
            self.stdout.feed_eof()
            self.returncode = code
            self.done.set()

        async def wait(self):
            await self.done.wait()
            return self.returncode

        def terminate(self):
            self.stdout.feed_eof()
            self.returncode = -1
            self.done.set()

    async def spawn(*args, **kwargs):
        assert args[1:3] == ("-X", "utf8")
        assert "--demo" in args and "--output" in args
        assert "PAPERPILOT_DATA_PASSWORD" not in kwargs["env"]
        run_id = kwargs["env"]["PAPERPILOT_EXPERIMENT_RUN_ID"]
        output = tmp_path / ".runtime/security-experiments" / run_id / "result.json"
        if not error:
            output.parent.mkdir(parents=True)
            output.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "scope": "disposable_postgres_security_experiment",
                        "run_id": str(uuid4()) if wrong_run else run_id,
                        "generated_at": datetime.now(UTC).isoformat(),
                        "configurations": [{}, {}, {}],
                        "accepted": code == 0,
                    }
                ),
                encoding="utf-8",
            )
        process = Process()
        processes.append(process)
        return process

    def cleanup(run_id):
        cleaned.append(run_id)
        if cleanup_error:
            raise RuntimeError("cleanup unavailable")

    monkeypatch.setattr(experiment.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(experiment, "cleanup_containers", cleanup)
    return processes, cleaned


@pytest.mark.parametrize(
    "code,error,wrong_run,cleanup_error,expected",
    [
        (0, None, False, False, None),
        (1, None, False, False, None),  # A completed experiment may measure a failed acceptance target.
        (2, "DOCKER_UNAVAILABLE", False, False, "DOCKER_UNAVAILABLE"),
        (2, "DOCKER_CONTAINER_FAILED", False, False, "DOCKER_CONTAINER_FAILED"),
        (0, None, True, False, "EXPERIMENT_INVALID_RESULT"),
        (0, None, False, True, "EXPERIMENT_CLEANUP_FAILED"),
    ],
)
def test_owned_run_results_and_failure_never_reuse_history(
    monkeypatch, tmp_path, code, error, wrong_run, cleanup_error, expected
):
    processes, cleaned = install_child(
        monkeypatch, tmp_path, code=code, error=error, wrong_run=wrong_run, cleanup_error=cleanup_error
    )

    async def scenario():
        runner = experiment.LocalSecurityExperiments()
        ctx = SimpleNamespace(project_id=uuid4(), user_id=uuid4())
        first = runner.start(ctx)
        assert runner.start(ctx).run_id == first.run_id
        foreign = SimpleNamespace(project_id=uuid4(), user_id=uuid4())
        assert runner.current(foreign).run_id is None
        with pytest.raises(Exception, match="另一个"):
            runner.start(foreign)
        await asyncio.sleep(0)
        await processes[0].finish()
        await runner.task
        state = runner.current(ctx)
        assert not state.busy and state.error == expected
        assert state.status == ("failed" if expected else "succeeded")
        assert (state.report is None) == (expected is not None)
        assert cleaned == [first.run_id]

    asyncio.run(scenario())


def test_shutdown_cancels_child_and_cleans_only_its_run(monkeypatch, tmp_path):
    processes, cleaned = install_child(monkeypatch, tmp_path)

    async def scenario():
        runner = experiment.LocalSecurityExperiments()
        state = runner.start(SimpleNamespace(project_id=uuid4(), user_id=uuid4()))
        await asyncio.sleep(0)
        await runner.close()
        assert processes[0].returncode == -1
        assert state.status == "failed" and state.error == "EXPERIMENT_CANCELLED" and state.report is None
        assert cleaned == [state.run_id]

    asyncio.run(scenario())


def test_child_has_no_business_or_model_secrets(monkeypatch):
    monkeypatch.setenv("PAPERPILOT_DATA_PASSWORD", "must-not-pass")
    monkeypatch.setenv("PAPERPILOT_AI_API_KEY", "must-not-pass")
    env = experiment.child_environment(uuid4())
    assert "PAPERPILOT_DATA_PASSWORD" not in env and "PAPERPILOT_AI_API_KEY" not in env


def test_deadline_terminates_child_and_discards_incomplete_result(monkeypatch, tmp_path):
    processes, cleaned = install_child(monkeypatch, tmp_path)
    original = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda _: original(0.02))

    async def scenario():
        runner = experiment.LocalSecurityExperiments()
        state = runner.start(SimpleNamespace(project_id=uuid4(), user_id=uuid4()))
        await runner.task
        assert state.error == "EXPERIMENT_TIMEOUT" and state.report is None and not state.busy
        assert processes[0].returncode == -1 and cleaned == [state.run_id]

    asyncio.run(scenario())


def test_shutdown_before_child_starts_is_safe():
    async def scenario():
        runner = experiment.LocalSecurityExperiments()
        state = runner.start(SimpleNamespace(project_id=uuid4(), user_id=uuid4()))
        await runner.close()
        assert not state.busy and state.error == "EXPERIMENT_CANCELLED"

    asyncio.run(scenario())


def test_cleanup_is_limited_to_exact_label_and_valid_container_ids(monkeypatch):
    calls, run_id = [], uuid4()

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout="a" * 64 + "\n")

    monkeypatch.setattr(experiment.subprocess, "run", run)
    experiment.cleanup_containers(run_id)
    assert calls[0][-1] == f"label={experiment.LABEL}={run_id}"
    assert calls[1] == ["docker", "rm", "--force", "a" * 64]


def test_preview_marker_requests_graceful_server_shutdown(tmp_path):
    launcher = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/run_security.py"))
    marker = tmp_path / "owned-preview.stop"
    marker.touch()
    server = SimpleNamespace(should_exit=False)
    launcher["stop_when_requested"](server, marker)
    assert server.should_exit
