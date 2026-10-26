"""Restart two actual HTTP server processes against one disposable SQLite database."""

import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.legacy_persistence


@contextmanager
def server(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = {
        "SYSTEMROOT": os.getenv("SYSTEMROOT", "C:\\Windows"),
        "PAPERPILOT_ENV": "test",
        "PAPERPILOT_A_MODE": "persistent",
        "PAPERPILOT_C_MODE": "mock",
        "PAPERPILOT_D_MODE": "mock",
        "PAPERPILOT_TEST_DATABASE_PATH": str(tmp_path / "restart.sqlite3"),
        "PAPERPILOT_STORAGE_DIR": str(tmp_path / "uploads"),
        "PAPERPILOT_LEASE_SECONDS": "1",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "legacy_sqlite_app:create_legacy_app",
            "--factory",
            "--app-dir",
            "backend/tests",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", headers={"X-PaperPilot-Client": "web"}, trust_env=False
        ) as client:
            for _ in range(120):
                assert process.poll() is None, "Isolated HTTP server failed to start"
                try:
                    if client.get("/api/v1/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("Isolated HTTP server did not become ready")
            yield client
    finally:
        process.terminate()
        process.wait(timeout=10)


def finished(client, root, task_id):
    for _ in range(100):
        response = client.get(f"{root}/tasks/{task_id}")
        assert response.status_code == 200
        task = response.json()
        if task["status"] == "succeeded":
            return task
        assert task["status"] != "failed", "Persistent task unexpectedly failed"
        time.sleep(0.03)
    raise AssertionError("Persistent task timed out")


def test_actual_server_restart_preserves_business_data_but_requires_login(tmp_path):
    with server(tmp_path) as first:
        assert first.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        project = first.post("/api/v1/projects", json={"name": "重启恢复测试"}).json()
        root = f"/api/v1/projects/{project['project_id']}"
        member = first.post(f"{root}/members", json={"username": "bob"})
        assert member.status_code == 201
        upload = first.post(
            f"{root}/papers",
            files={"file": ("test.pdf", b"%PDF-1.4\nIsolated restart fixture", "application/pdf")},
            data={"title": "重启后仍保留", "year": "2024"},
        )
        assert upload.status_code == 202
        paper_id = upload.json()["paper"]["paper_id"]
        task_id = upload.json()["task"]["task_id"]
        finished(first, root, task_id)
        detail = first.get(f"{root}/papers/{paper_id}").json()
        sources = detail["sources"]
        evidence_task = first.post(f"{root}/papers/{paper_id}/evidence").json()
        finished(first, root, evidence_task["task_id"])
        evidence = first.get(f"{root}/papers/{paper_id}/evidence").json()["items"]
        payload = {
            "title": "持久化报告",
            "research_question": "重启",
            "body": "版本一 [1]",
            "sources": sources,
        }
        version = first.post(f"{root}/reports", json=payload)
        assert version.status_code == 201
        version = version.json()
        payload.update(
            report_id=version["report_id"], base_version_id=version["version_id"], body="版本二 [1]"
        )
        assert first.post(f"{root}/reports", json=payload).status_code == 201
        audit = first.get(f"{root}/audit-events", params={"limit": 100}).json()
        assert audit["total"] > 0
        old_cookie = first.cookies.get("pp_session")

    with server(tmp_path) as second:
        second.cookies.set("pp_session", old_cookie)
        assert second.get("/api/v1/projects").status_code == 401
        second.cookies.clear()
        assert second.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        assert any(
            p["project_id"] == project["project_id"] for p in second.get("/api/v1/projects").json()["items"]
        )
        assert any(m["user"]["username"] == "bob" for m in second.get(f"{root}/members").json()["items"])
        assert second.get(f"{root}/papers/{paper_id}").json() == detail
        assert second.get(f"{root}/tasks/{task_id}").json()["status"] == "succeeded"
        assert second.get(f"{root}/papers/{paper_id}/evidence").json()["items"] == evidence
        versions = second.get(f"{root}/reports/{version['report_id']}/versions").json()["items"]
        assert [v["body"] for v in versions] == ["版本一 [1]", "版本二 [1]"]
        assert all(v["sources"] == sources for v in versions)
        recovered = second.get(f"{root}/audit-events", params={"limit": 100}).json()
        assert {e["event_id"] for e in audit["items"]} <= {e["event_id"] for e in recovered["items"]}
        health = second.get("/api/v1/health").json()
        assert health["modes"]["a"] == "persistent"
        assert "in_memory_sessions_lost_on_restart" in health["limitations"]
