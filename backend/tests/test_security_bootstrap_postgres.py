"""Real identity-backed startup; no browser or production deployment."""

from contextlib import nullcontext
from dataclasses import replace
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.security import bootstrap
from app.security.provision import create_login, install
from app.security.settings import SecuritySettings
from security_contracts.postgres_provider import postgres_workspace
from test_security_team_integration import c_reference, local_model

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        os.getenv("PAPERPILOT_SECURITY_POSTGRES_TESTS") != "1",
        reason="Startup tests require explicit PostgreSQL opt-in and pinned A source",
    ),
]


@pytest.fixture(scope="module")
def pg(record_testsuite_property):
    import psycopg

    with postgres_workspace(record_testsuite_property) as workspace:
        audit_role, audit_secret = "pp_audit_" + uuid4().hex, secrets.token_urlsafe(32)
        reader_role, reader_secret = "pp_reader_" + uuid4().hex, secrets.token_urlsafe(32)
        with psycopg.connect(**workspace.config.conninfo(), autocommit=True) as admin:
            install(admin, existing_views=True)
            create_login(admin, audit_role, audit_secret)
            create_login(admin, reader_role, reader_secret, project_id=workspace.contexts[0].project_id)
        workspace.security_document = {
            "database": {
                "host": workspace.config.host,
                "port": workspace.config.port,
                "dbname": workspace.config.dbname,
            },
            "audit": {"user": audit_role, "password_env": "TEST_BOOT_AUDIT_PASSWORD"},
            "readers": [
                {
                    "project_id": str(workspace.contexts[0].project_id),
                    "user": reader_role,
                    "password_env": "TEST_BOOT_READER_PASSWORD",
                }
            ],
        }
        workspace.identity_secrets = {
            "TEST_BOOT_AUDIT_PASSWORD": audit_secret,
            "TEST_BOOT_READER_PASSWORD": reader_secret,
        }
        yield workspace


def environment(pg, tmp_path, monkeypatch):
    config = tmp_path / "security.json"
    config.write_text(json.dumps(pg.security_document), encoding="utf-8")
    values = {
        "PAPERPILOT_ENV": "test",
        "PAPERPILOT_AUTH_MODE": "development",
        "PAPERPILOT_A_MODE": "persistent",
        "PAPERPILOT_C_MODE": "mock",
        "PAPERPILOT_D_MODE": "real",
        "PAPERPILOT_SECURITY_CONFIG": str(config),
        "PAPERPILOT_STORAGE_DIR": str(tmp_path / "uploads"),
        "PAPERPILOT_DATA_HOST": pg.config.host,
        "PAPERPILOT_DATA_PORT": str(pg.config.port),
        "PAPERPILOT_DATA_DBNAME": pg.config.dbname,
        "PAPERPILOT_DATA_USER": pg.config.user,
        "PAPERPILOT_DATA_PASSWORD": pg.config.password,
        **pg.identity_secrets,
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return config


@pytest.mark.parametrize("c_mode", ["mock", "real"])
def test_explicit_bootstrap_runs_a_d_and_a_c_d_and_closes_resources(pg, tmp_path, monkeypatch, c_mode):
    if c_mode == "real" and os.getenv("PAPERPILOT_SECURITY_TEAM_TESTS") != "1":
        pytest.skip("C bootstrap additionally requires explicit team opt-in and pinned C source")
    environment(pg, tmp_path, monkeypatch)
    captured = []
    original = bootstrap.compose_audit

    def compose(config):
        binding = original(config)
        captured.append(binding)
        return binding

    monkeypatch.setattr(bootstrap, "compose_audit", compose)
    with c_reference() if c_mode == "real" else nullcontext(), local_model() as model:
        if c_mode == "real":
            monkeypatch.setenv("PAPERPILOT_C_MODE", "real")
            for key, value in {
                "PAPERPILOT_AI_BASE_URL": model.url,
                "PAPERPILOT_AI_API_KEY": "local-test-key",
                "PAPERPILOT_AI_CHAT_MODEL": "test-chat",
                "PAPERPILOT_AI_EMBEDDING_MODEL": "test-embedding",
                "PAPERPILOT_AI_EMBEDDING_REVISION": "test-v1",
                "PAPERPILOT_AI_EMBEDDING_DIMENSIONS": "3",
                "PAPERPILOT_AI_EMBEDDING_BACKEND": "remote",
            }.items():
                monkeypatch.setenv(key, value)
        application = bootstrap.create_app()
        assert application.state.adapters.ai.__class__.__name__ == (
            "RealAI" if c_mode == "real" else "MockAI"
        )
        base = f"/api/v1/projects/{pg.contexts[0].project_id}"
        with TestClient(application, headers={"X-PaperPilot-Client": "web"}) as client:
            assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
            # Business audit uses A's port; D's two critical events must use its dedicated writer.
            append = application.state.adapters.data.append_audit

            def business_append(event):
                assert not event.action.startswith("security.query.")
                return append(event)

            monkeypatch.setattr(application.state.adapters.data, "append_audit", business_append)
            result = client.post(base + "/secure-queries", json={"question": "按年份统计论文数量"})
            assert result.status_code == 200 and result.json()["execution_status"] == "executed"
            audit = client.get(base + "/audit-events", params={"action": "security.query.finished"})
            assert any(event["request_id"] == result.json()["request_id"] for event in audit.json()["items"])
            if c_mode == "real":
                assert (
                    client.post(base + "/search", json={"query": "contract", "mode": "keyword"}).status_code
                    == 200
                )
        assert application.state.adapters.data.database._closed
        # The captured bound append retains A's audit adapter, whose pool must also be closed.
        assert captured[0].append.__self__.database._closed


@pytest.mark.parametrize(
    "failure", ["missing_secret", "wrong_secret", "same_secret", "wrong_database", "wrong_modes"]
)
def test_startup_failures_are_safe_and_close_created_resources(pg, tmp_path, monkeypatch, failure):
    config_path = environment(pg, tmp_path, monkeypatch)
    captured = []
    original = bootstrap.compose_audit

    def compose(config):
        binding = original(config)
        captured.append(binding)
        return binding

    monkeypatch.setattr(bootstrap, "compose_audit", compose)
    if failure == "missing_secret":
        monkeypatch.delenv("TEST_BOOT_AUDIT_PASSWORD")
    elif failure == "wrong_secret":
        monkeypatch.setenv("TEST_BOOT_READER_PASSWORD", "PRIVATE_WRONG_SECRET")
    elif failure == "same_secret":
        monkeypatch.setenv("TEST_BOOT_READER_PASSWORD", pg.identity_secrets["TEST_BOOT_AUDIT_PASSWORD"])
    elif failure == "wrong_database":
        document = json.loads(config_path.read_text(encoding="utf-8"))
        document["database"]["dbname"] = "wrong_database"
        config_path.write_text(json.dumps(document), encoding="utf-8")
    else:
        monkeypatch.setenv("PAPERPILOT_D_MODE", "mock")
    with pytest.raises(RuntimeError) as error:
        bootstrap.create_app()
    assert "no Mock fallback" in str(error.value) and "PRIVATE" not in str(error.value)
    assert all(binding.append.__self__.database._closed for binding in captured)


def test_real_launcher_process_uses_trusted_configuration(pg, tmp_path, monkeypatch):
    environment(pg, tmp_path, monkeypatch)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    root = Path(__file__).resolve().parents[2]
    process = subprocess.Popen(
        [
            sys.executable,
            str(root / "scripts/run_security.py"),
            "--a-source",
            os.environ["PAPERPILOT_SECURITY_TEST_A_SOURCE"],
            "--port",
            str(port),
        ],
        cwd=root,
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=1) as response:
                    health = json.load(response)
                    assert health["modes"] == {"a": "persistent", "c": "mock", "d": "real"}
                    break
            except (URLError, TimeoutError):
                assert process.poll() is None, "Launcher exited before readiness"
                assert time.monotonic() < deadline, "Launcher did not become ready"
                time.sleep(0.05)
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
    assert all(secret not in stdout + stderr for secret in pg.identity_secrets.values())


def test_configuration_resolution_rejects_business_identity_alias(pg, tmp_path, monkeypatch):
    environment(pg, tmp_path, monkeypatch)
    config = SecuritySettings.from_env()
    aliased = replace(pg.config, user=config.audit.user)
    with pytest.raises(RuntimeError, match="security identity secrets"):
        config.resolve(aliased)
