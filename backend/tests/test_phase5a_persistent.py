"""Real PostgreSQL + Mock C/D in a specifically authorized isolated test server."""

import io
import os
import socket
import time

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from ai_contracts.support import pdf_bytes
from app.config import Settings
from app.data.database import PostgresDatabase
from app.data.provider import disposable_database
from app.dev_composition import create_app
from app.errors import AppError

pytestmark = pytest.mark.real_database
WEB = {"X-PaperPilot-Client": "web"}
PDF = pdf_bytes(["A reproducible text PDF for isolated PostgreSQL integration."])


@pytest.fixture
def database(monkeypatch, tmp_path):
    if os.getenv("PAPERPILOT_PHASE5A_ISOLATED") != "1":
        pytest.skip("requires explicit isolated Phase 5A PostgreSQL server")
    with disposable_database("paperpilot_phase5a_") as config:
        db = PostgresDatabase(config)
        try:
            db.migrate()
            db.seed_development_users()
        finally:
            db.close()
        for key, value in {
            "PAPERPILOT_DATA_HOST": config.host,
            "PAPERPILOT_DATA_PORT": str(config.port),
            "PAPERPILOT_DATA_USER": config.user,
            "PAPERPILOT_DATA_PASSWORD": config.password,
            "PAPERPILOT_DATA_DBNAME": config.dbname,
        }.items():
            monkeypatch.setenv(key, value)
        yield config, tmp_path


def wait_task(client, project, task):
    for _ in range(200):
        body = client.get(f"/api/v1/projects/{project}/tasks/{task}").json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.05)
    pytest.fail("task did not finish")


def upload(client, project, key):
    return client.post(
        f"/api/v1/projects/{project}/papers",
        files={"file": ("text.pdf", PDF, "application/pdf")},
        data={"title": "Phase 5A text PDF"},
        headers={"Idempotency-Key": key},
    )


@pytest.mark.parametrize("c,d", [("mock", "mock"), ("real", "mock"), ("real", "real")])
def test_real_database_composes_explicit_modules(database, monkeypatch, c, d):
    _, storage = database
    for key, value in {
        "PAPERPILOT_AI_BASE_URL": "http://127.0.0.1:9",
        "PAPERPILOT_AI_API_KEY": "fixture-only",
        "PAPERPILOT_AI_CHAT_MODEL": "fixture",
        "PAPERPILOT_AI_EMBEDDING_MODEL": "fixture",
        "PAPERPILOT_AI_EMBEDDING_DIMENSIONS": "3",
    }.items():
        monkeypatch.setenv(key, value)
    settings = Settings(environment="test", a_mode="persistent", c_mode=c, d_mode=d, storage_dir=storage)
    with TestClient(create_app(settings), headers=WEB) as client:
        assert client.get("/api/v1/health").json()["modes"] == {"a": "persistent", "c": c, "d": d}
        if d == "real":
            client.post("/api/v1/auth/login", json={"username": "alice"})
            project = client.post("/api/v1/projects", json={"name": "Policy mode"}).json()["project_id"]
            result = client.post(
                f"/api/v1/projects/{project}/secure-queries", json={"question": "按年份统计论文数量"}
            )
            assert result.status_code == 200
            assert (
                result.json()["decision"] == "allowed" and result.json()["execution_status"] == "not_executed"
            )
            assert not result.json()["rows"]


def test_persistence_restart_outbox_and_idempotency(database):
    config, storage = database
    assert "reproducible text PDF" in PdfReader(io.BytesIO(PDF)).pages[0].extract_text()
    settings = Settings(environment="test", a_mode="persistent", storage_dir=storage)
    app = create_app(settings)
    with TestClient(app, headers=WEB) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        project = client.post("/api/v1/projects", json={"name": "Phase 5A"}).json()["project_id"]
        assert len(client.get(f"/api/v1/projects/{project}/members").json()["items"]) == 1
        first = upload(client, project, "phase5a-upload")
        assert first.status_code == 202
        paper, task = first.json()["paper"]["paper_id"], first.json()["task"]["task_id"]
        replay = upload(client, project, "phase5a-upload")
        assert (replay.json()["paper"]["paper_id"], replay.json()["task"]["task_id"]) == (paper, task)
        assert wait_task(client, project, task)["status"] == "succeeded"
        detail = client.get(f"/api/v1/projects/{project}/papers/{paper}").json()
        assert detail["paper"]["mock"] is False and detail["sources"][0]["synthetic"] is True
        version = client.post(
            f"/api/v1/projects/{project}/reports",
            json={
                "title": "Snapshot",
                "research_question": "Question",
                "body": "Fixture [1]",
                "sources": detail["sources"],
            },
        ).json()
        from app.data.deliverer import build_deliverer

        deliverer = build_deliverer(config)
        try:
            assert deliverer.pending_count() > 0
            assert deliverer.run_once() > 0
        finally:
            deliverer.database.close()
        assert client.get(f"/api/v1/projects/{project}/audit-events").json()["total"] > 0
    with TestClient(create_app(settings), headers=WEB) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        assert client.get(f"/api/v1/projects/{project}/papers/{paper}").status_code == 200
        assert client.get(f"/api/v1/projects/{project}/tasks/{task}").json()["status"] == "succeeded"
        history = client.get(f"/api/v1/projects/{project}/reports/{version['report_id']}/versions").json()[
            "items"
        ]
        assert history[0]["sources"] == detail["sources"]
        replay = upload(client, project, "phase5a-upload")
        assert (replay.json()["paper"]["paper_id"], replay.json()["task"]["task_id"]) == (paper, task)
        assert len(client.get(f"/api/v1/projects/{project}/papers").json()["items"]) == 1


def test_lost_commit_ack_retains_adopted_file_and_reconciles(database, monkeypatch):
    _, storage = database
    app = create_app(Settings(environment="test", a_mode="persistent", storage_dir=storage))
    with TestClient(app, headers=WEB) as client:
        client.post("/api/v1/auth/login", json={"username": "alice"})
        project = client.post("/api/v1/projects", json={"name": "Unknown commit"}).json()["project_id"]
        data = app.state.adapters.data
        original = data.import_paper

        def lost_ack(*args):
            original(*args)
            raise AppError(503, "DATA_COMMIT_UNKNOWN", "test acknowledgement lost")

        monkeypatch.setattr(data, "import_paper", lost_ack)
        response = upload(client, project, "phase5a-unknown")
        assert response.status_code == 503 and response.json()["code"] == "DATA_COMMIT_UNKNOWN"
        papers = client.get(f"/api/v1/projects/{project}/papers").json()["items"]
        assert len(papers) == 1 and (storage / f"{papers[0]['paper_id']}.pdf").is_file()
        monkeypatch.setattr(data, "import_paper", original)
        replay = upload(client, project, "phase5a-unknown")
        assert replay.status_code == 202 and replay.json()["paper"]["paper_id"] == papers[0]["paper_id"]


def test_postgres_transaction_rollback_leaves_no_paper_or_file(database, monkeypatch):
    _, storage = database
    app = create_app(Settings(environment="test", a_mode="persistent", storage_dir=storage))
    with TestClient(app, headers=WEB) as client:
        client.post("/api/v1/auth/login", json={"username": "alice"})
        project = client.post("/api/v1/projects", json={"name": "Rollback"}).json()["project_id"]

        def fail_before_commit(operation, connection):
            if operation == "import_paper":
                raise AppError(503, "DATA_UNAVAILABLE", "fixture rollback")

        monkeypatch.setattr(app.state.adapters.data, "_before_commit", fail_before_commit)
        response = upload(client, project, "phase5a-rollback")
        assert response.status_code == 503 and response.json()["code"] == "DATA_UNAVAILABLE"
        assert client.get(f"/api/v1/projects/{project}/papers").json()["items"] == []
        assert not list(storage.glob("*.pdf"))


def test_unmigrated_database_is_refused_without_automatic_migration(monkeypatch, tmp_path):
    if os.getenv("PAPERPILOT_PHASE5A_ISOLATED") != "1":
        pytest.skip("requires explicit isolated Phase 5A PostgreSQL server")
    with disposable_database("paperpilot_phase5a_unmigrated_") as config:
        for key, value in {
            "PAPERPILOT_DATA_HOST": config.host,
            "PAPERPILOT_DATA_PORT": str(config.port),
            "PAPERPILOT_DATA_USER": config.user,
            "PAPERPILOT_DATA_PASSWORD": config.password,
            "PAPERPILOT_DATA_DBNAME": config.dbname,
        }.items():
            monkeypatch.setenv(key, value)
        with pytest.raises(RuntimeError, match="readiness failed"):
            create_app(Settings(environment="test", a_mode="persistent", storage_dir=tmp_path))
        db = PostgresDatabase(config)
        try:
            with db.transaction() as connection:
                assert (
                    connection.execute("SELECT to_regclass('public.schema_migrations') AS name").fetchone()[
                        "name"
                    ]
                    is None
                )
        finally:
            db.close()


def test_connection_failure_is_explicit_and_never_falls_back(database, monkeypatch):
    _, storage = database
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        unused_port = probe.getsockname()[1]
    monkeypatch.setenv("PAPERPILOT_DATA_PORT", str(unused_port))
    with pytest.raises(RuntimeError, match="readiness failed") as error:
        create_app(Settings(environment="test", a_mode="persistent", storage_dir=storage))
    assert os.environ["PAPERPILOT_DATA_PASSWORD"] not in str(error.value)
