import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def pytest_addoption(parser):
    parser.addoption(
        "--data-contract-provider",
        default=None,
        help="Explicit isolated test provider module:factory; defaults to Mock",
    )
    parser.addoption(
        "--legacy-persistence", action="store_true", help="Opt in to historical SQLite experiments"
    )
    parser.addoption(
        "--ai-contract-provider", default=None, help="Explicit isolated C provider module:factory"
    )
    parser.addoption(
        "--security-contract-provider", default=None, help="Explicit isolated D provider module:factory"
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--legacy-persistence"):
        skip = pytest.mark.skip(
            reason="历史 SQLite 实验；仅 --legacy-persistence 显式运行，不是正式数据库验收"
        )
        for item in items:
            if "legacy_persistence" in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(environment="test", storage_dir=tmp_path / "uploads"))


@pytest.fixture
def client(app):
    with TestClient(app, headers={"X-PaperPilot-Client": "web"}) as value:
        yield value


@pytest.fixture
def signed(client):
    assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
    return client


@pytest.fixture
def project(signed):
    return signed.get("/api/v1/projects").json()["items"][0]["project_id"]


def finished(client, project, task_id):
    for _ in range(80):
        response = client.get(f"/api/v1/projects/{project}/tasks/{task_id}")
        assert response.status_code == 200
        task = response.json()
        if task["status"] in {"succeeded", "failed"}:
            return task
        time.sleep(0.03)
    raise AssertionError("Mock task did not finish")
