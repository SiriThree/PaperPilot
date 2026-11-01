"""Deployment boundary and backup integrity checks; no existing deployment is modified."""

from datetime import UTC, datetime
import hashlib
import hmac
import json
from pathlib import Path
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import local_deployment as deployment  # noqa: E402
import local_operations as operations  # noqa: E402


def test_runtime_environment_does_not_inherit_admin_demo_or_model_settings(monkeypatch):
    monkeypatch.setenv("PAPERPILOT_DATA_PASSWORD", "private-administrator")
    monkeypatch.setenv("PAPERPILOT_AI_API_KEY", "unselected-model")
    result = deployment.runtime_environment(
        {
            "environment": {
                "PAPERPILOT_AUTH_MODE": "credentials",
                "PAPERPILOT_DATA_PASSWORD_FILE": "/explicit/runtime",
            }
        }
    )
    assert "PAPERPILOT_DATA_PASSWORD" not in result and "PAPERPILOT_AI_API_KEY" not in result
    assert result["PAPERPILOT_DATA_PASSWORD_FILE"] == "/explicit/runtime"


def test_initialization_never_adopts_existing_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(deployment, "STATE", tmp_path)
    with pytest.raises(ValueError):
        deployment.init()


def test_conflicting_owned_operations_are_refused(tmp_path):
    path = tmp_path / "operation.lock"
    with deployment.file_lock(path, timeout=0):
        with pytest.raises(RuntimeError):
            with deployment.file_lock(path, timeout=0):
                pytest.fail("Concurrent maintenance must never start")
    with deployment.file_lock(path, timeout=0):
        pass


@pytest.mark.parametrize("tamper", ["data", "manifest", "extra", "missing"])
def test_backup_tampering_is_rejected(tmp_path, monkeypatch, tamper):
    monkeypatch.setattr(operations, "STATE", tmp_path)
    (tmp_path / "secrets").mkdir()
    folder = tmp_path / "backups/one"
    folder.mkdir(parents=True)
    data = folder / "database.dump"
    data.write_bytes(b"non-secret fixture")
    manifest = {"created_at": datetime.now(UTC).isoformat(), "files": operations.inventory(folder)}
    body = json.dumps(manifest).encode()
    (folder / "manifest.json").write_bytes(body)
    (folder / "manifest.hmac").write_text(
        hmac.new(operations.integrity_key(), body, hashlib.sha256).hexdigest()
    )
    assert operations.verify_backup(folder) == manifest
    if tamper == "data":
        data.write_bytes(b"changed")
    elif tamper == "manifest":
        (folder / "manifest.json").write_text("{}")
    elif tamper == "extra":
        (folder / "extra").write_text("changed")
    else:
        data.unlink()
    with pytest.raises(ValueError):
        operations.verify_backup(folder)


def test_alerts_cover_missing_backup_and_failed_services():
    assert set(operations.alert_reasons({})) == {
        "WEB_UNAVAILABLE",
        "DATABASE_UNAVAILABLE",
        "BACKUP_STALE",
        "LOW_DISK_SPACE",
        "TLS_CERTIFICATE_EXPIRING",
    }
    good = {
        "web_ready": True,
        "database_ready": True,
        "backup_age_hours": 1,
        "disk_free_bytes": 10 * 1024**3,
        "certificate_days_remaining": 100,
    }
    assert operations.alert_reasons(good) == []
    assert operations.alert_reasons(dict(good, dead_letters=1, expired_tasks=1)) == [
        "OUTBOX_DEAD_LETTERS",
        "EXPIRED_WORK_LEASE",
    ]


def test_frontend_history_fallback_keeps_api_misses_and_private_files_out(tmp_path, monkeypatch):
    import app.deployment as web

    public = tmp_path / "public"
    (public / "assets").mkdir(parents=True)
    (public / "index.html").write_text("<html>Public app</html>")
    (tmp_path / "secret").write_text("PRIVATE_CONTENT")
    monkeypatch.setenv("PAPERPILOT_FRONTEND_DIR", str(public))
    monkeypatch.setattr(web, "compose_app", FastAPI)
    with TestClient(web.create_app()) as client:
        assert "Public app" in client.get("/projects/one/security").text
        assert client.get("/api/v1/unknown").status_code == 404
        assert "PRIVATE_CONTENT" not in client.get("/../secret").text


def test_production_refuses_demo_auth_and_non_https_origins():
    from app.config import Settings

    with pytest.raises(RuntimeError):
        Settings(environment="production", a_mode="persistent", c_mode="real", d_mode="real").validate()
    with pytest.raises(RuntimeError):
        Settings(
            environment="production", a_mode="persistent", c_mode="real", d_mode="real", auth_mode="password"
        ).validate()
    with pytest.raises(RuntimeError, match="Production"):
        Settings(
            environment="production",
            a_mode="persistent",
            c_mode="real",
            d_mode="real",
            auth_mode="credentials",
            allowed_origins=("https://localhost:8443",),
        ).validate()
