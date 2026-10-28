"""Trusted configuration parsing: no secrets in files or validation diagnostics."""

from copy import deepcopy
import json
from uuid import uuid4

import pytest

from app.security.settings import SecuritySettings


def document():
    return {
        "database": {"host": "127.0.0.1", "port": 5432, "dbname": "test"},
        "audit": {"user": "pp_audit_gateway", "password_env": "TEST_AUDIT_PASSWORD"},
        "readers": [
            {"project_id": str(uuid4()), "user": "pp_reader_project", "password_env": "TEST_READER_PASSWORD"}
        ],
    }


def test_config_file_contains_references_and_uuid_bindings_only(tmp_path, monkeypatch):
    path = tmp_path / "security.json"
    value = document()
    path.write_text(json.dumps(value), encoding="utf-8-sig")
    monkeypatch.setenv("PAPERPILOT_SECURITY_CONFIG", str(path))
    config = SecuritySettings.from_env()
    assert str(config.readers[0].project_id) == value["readers"][0]["project_id"]
    assert config.audit.password_env == "TEST_AUDIT_PASSWORD"


@pytest.mark.parametrize(
    "mutation",
    [
        "password",
        "unknown",
        "duplicate_project",
        "duplicate_user",
        "duplicate_secret",
        "empty_readers",
        "bad_port",
        "admin_login",
        "bad_secret_name",
    ],
)
def test_invalid_configuration_is_rejected_without_echoing_values(tmp_path, monkeypatch, mutation):
    value = document()
    if mutation == "password":
        value["audit"]["password"] = "PRIVATE_SECRET"
    elif mutation == "unknown":
        value["untrusted"] = "PRIVATE_SECRET"
    elif mutation.startswith("duplicate"):
        extra = deepcopy(value["readers"][0])
        if mutation != "duplicate_project":
            extra["project_id"] = str(uuid4())
        if mutation != "duplicate_user":
            extra["user"] = "pp_reader_second"
        if mutation != "duplicate_secret":
            extra["password_env"] = "SECOND_PASSWORD"
        value["readers"].append(extra)
    elif mutation == "empty_readers":
        value["readers"] = []
    elif mutation == "bad_port":
        value["database"]["port"] = True
    elif mutation == "admin_login":
        value["audit"]["user"] = "postgres"
    else:
        value["audit"]["password_env"] = "PRIVATE_SECRET\nINJECTION"
    path = tmp_path / "security.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setenv("PAPERPILOT_SECURITY_CONFIG", str(path))
    with pytest.raises(RuntimeError) as error:
        SecuritySettings.from_env()
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize(
    "body",
    ['{"audit":{},"audit":{}}', "x" * (64 * 1024 + 1), "{broken"],
    ids=["duplicate", "oversize", "malformed"],
)
def test_duplicate_keys_excessive_or_invalid_json_fail_closed(tmp_path, monkeypatch, body):
    path = tmp_path / "security.json"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setenv("PAPERPILOT_SECURITY_CONFIG", str(path))
    with pytest.raises(RuntimeError):
        SecuritySettings.from_env()


def test_configuration_requires_explicit_absolute_existing_path(monkeypatch):
    for value in (None, "relative-security.json"):
        if value is None:
            monkeypatch.delenv("PAPERPILOT_SECURITY_CONFIG", raising=False)
        else:
            monkeypatch.setenv("PAPERPILOT_SECURITY_CONFIG", value)
        with pytest.raises(RuntimeError):
            SecuritySettings.from_env()
