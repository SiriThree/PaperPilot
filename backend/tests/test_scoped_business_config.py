"""Protected scoped-business manifest, independent of any database server."""

import json
import os
import subprocess
from uuid import uuid4

import pytest

from app.data.database import ConnectionConfig
from app.data.scoped_business_config import scoped_business_configurations


def protected_file(path, contents):
    path.write_text(contents, encoding="utf-8")
    if os.name == "nt":
        script = r"""
          $ErrorActionPreference = 'Stop'
          $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
          $acl = [System.Security.AccessControl.FileSecurity]::new()
          $acl.SetOwner($user)
          $acl.SetAccessRuleProtection($true, $false)
          $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($user, 'FullControl', 'Allow'))
          [System.IO.File]::SetAccessControl($env:PAPERPILOT_TEST_MANIFEST, $acl)
        """
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "PAPERPILOT_TEST_MANIFEST": str(path)},
            capture_output=True,
            timeout=15,
        )
        assert result.returncode == 0
    else:
        path.chmod(0o600)
    return path


@pytest.fixture
def manifest(tmp_path):
    user, project = uuid4(), uuid4()
    entries = [
        {
            "db_user": "reader",
            "db_password": "fixture-secret-a",
            "user_id": str(user),
            "project_id": str(project),
            "profile": "read",
        },
        {
            "db_user": "writer",
            "db_password": "fixture-secret-b",
            "user_id": str(user),
            "project_id": str(uuid4()),
            "profile": "report",
        },
    ]
    path = protected_file(tmp_path / "mapping.json", json.dumps(entries))
    env = {
        "PAPERPILOT_SCOPED_BUSINESS_ISOLATED_TEST": "1",
        "PAPERPILOT_SCOPED_BUSINESS_BINDINGS_FILE": str(path),
        "PAPERPILOT_SCOPED_BUSINESS_FUNCTION_OWNER": "function_owner",
        "PAPERPILOT_SCOPED_BUSINESS_BINDING_OWNER": "binding_owner",
    }
    return env, entries, path


def test_protected_manifest_and_distinct_identities(manifest):
    env, entries, _ = manifest
    connection = ConnectionConfig("127.0.0.1", 5432, "shared", "fixture-secret", "isolated")
    mappings = scoped_business_configurations(env, connection, forbidden_users={"auth", "audit"})
    assert [profile for _, profile in mappings] == ["read", "report"]
    assert [config.connection.user for config, _ in mappings] == ["reader", "writer"]
    assert str(mappings[1][0].project_id) == entries[1]["project_id"]
    assert "fixture-secret" not in repr(mappings)


@pytest.mark.parametrize(
    "case", ["duplicate_login", "duplicate_scope", "wrong_profile", "extra_key", "forbidden", "bad_uuid"]
)
def test_manifest_rejects_invalid_and_colliding_identity(manifest, case):
    env, entries, path = manifest
    if case == "duplicate_login":
        entries[1]["db_user"] = entries[0]["db_user"]
    elif case == "duplicate_scope":
        entries[1]["project_id"] = entries[0]["project_id"]
    elif case == "wrong_profile":
        entries[1]["profile"] = "worker"
    elif case == "extra_key":
        entries[1]["unexpected"] = "value"
    elif case == "forbidden":
        entries[1]["db_user"] = "audit"
    else:
        entries[1]["project_id"] = "bad"
    path.write_text(json.dumps(entries), encoding="utf-8")
    connection = ConnectionConfig("localhost", 5432, "shared", "fixture-secret", "isolated")
    with pytest.raises(RuntimeError, match="Invalid isolated") as error:
        scoped_business_configurations(env, connection, forbidden_users={"audit"})
    assert "fixture-secret" not in str(error.value)


def test_configuration_refuses_unprotected_or_remote(manifest, tmp_path):
    env, entries, _ = manifest
    connection = ConnectionConfig("127.0.0.1", 5432, "shared", "fixture-secret", "isolated")
    with pytest.raises(RuntimeError):
        scoped_business_configurations(
            {**env, "PAPERPILOT_SCOPED_BUSINESS_BINDINGS": json.dumps(entries)}, connection
        )
    with pytest.raises(RuntimeError):
        scoped_business_configurations({**env, "PAPERPILOT_SCOPED_BUSINESS_ISOLATED_TEST": "0"}, connection)
    with pytest.raises(RuntimeError):
        scoped_business_configurations(env, replace_host(connection, "db.example"))
    with pytest.raises(RuntimeError, match="Unexpected"):
        scoped_business_configurations({**env, "PAPERPILOT_SCOPED_BUSINESS_UNKNOWN": "1"}, connection)
    unsafe = tmp_path / "unsafe"
    unsafe.write_text(json.dumps(entries), encoding="utf-8")
    if os.name != "nt":
        unsafe.chmod(0o644)
    with pytest.raises(RuntimeError):
        scoped_business_configurations(
            {**env, "PAPERPILOT_SCOPED_BUSINESS_BINDINGS_FILE": str(unsafe)}, connection
        )


def test_manifest_rejects_duplicate_json_object_keys(manifest):
    env, entries, path = manifest
    raw = json.dumps(entries).replace('"profile": "read"', '"profile": "read", "profile": "report"')
    path.write_text(raw, encoding="utf-8")
    connection = ConnectionConfig("127.0.0.1", 5432, "shared", "fixture-secret", "isolated")
    with pytest.raises(RuntimeError, match="Invalid isolated"):
        scoped_business_configurations(env, connection)


def replace_host(connection, host):
    from dataclasses import replace

    return replace(connection, host=host)


def test_router_initialization_cleans_up_previously_created_readers(monkeypatch):
    from app.data import scoped_business_config

    closed = []

    class Reader:
        def __init__(self, config):
            if config == "broken":
                raise RuntimeError("controlled initialization failure")
            self.config = config

        def close(self):
            closed.append(self.config)
            raise RuntimeError("controlled close failure")

    monkeypatch.setitem(scoped_business_config.PROFILES, "fixture", Reader)
    with pytest.raises(RuntimeError, match="controlled initialization failure"):
        scoped_business_config.build_scoped_business_router([("created", "fixture"), ("broken", "fixture")])
    assert closed == ["created"]
