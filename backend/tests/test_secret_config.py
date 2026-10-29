"""Secret sources reject ambiguous or unsafe configuration without disclosing values."""

import os

import pytest

from app.secret_config import secret_value


def test_direct_value_and_ambiguous_sources():
    assert secret_value({"TEST_SECRET": "example"}, "TEST_SECRET") == "example"
    with pytest.raises(RuntimeError, match="Conflicting"):
        secret_value({"TEST_SECRET": "example", "TEST_SECRET_FILE": "/unused"}, "TEST_SECRET")


def test_unsupported_platform_refuses_file(monkeypatch):
    import app.secret_config as config

    monkeypatch.setattr(config.os, "name", "nt")
    monkeypatch.setattr(config.sys, "platform", "unsupported")
    with pytest.raises(RuntimeError, match="unavailable"):
        secret_value({"TEST_SECRET_FILE": "/example"}, "TEST_SECRET")


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner/mode semantics require a POSIX host")
def test_posix_secret_file_rejects_excess_permissions_symlinks_and_oversized(tmp_path):
    path = tmp_path / "secret"
    path.write_text("fixture-value\n", encoding="utf-8")
    path.chmod(0o600)
    assert secret_value({"TEST_SECRET_FILE": str(path)}, "TEST_SECRET") == "fixture-value"
    path.chmod(0o644)
    with pytest.raises(RuntimeError):
        secret_value({"TEST_SECRET_FILE": str(path)}, "TEST_SECRET")
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(RuntimeError):
        secret_value({"TEST_SECRET_FILE": str(link)}, "TEST_SECRET")
    path.write_text("x" * 101, encoding="utf-8")
    with pytest.raises(RuntimeError):
        secret_value({"TEST_SECRET_FILE": str(path)}, "TEST_SECRET", limit=100)


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner/mode semantics require a POSIX host")
def test_protected_secret_files_feed_the_existing_composition_parsers(tmp_path):
    import json
    from uuid import uuid4

    from app.data.authentication_boundary import auth_config_from_env
    from app.data.database import ConnectionConfig
    from app.data.scoped_audit import scoped_audit_configurations
    from app.security.restricted_executor import restricted_configurations
    from app.security.view_baseline import VIEW_SHA256

    def protected(name, value):
        path = tmp_path / name
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
        return str(path)

    project, user = uuid4(), uuid4()
    auth_file = protected("authentication", "test-auth-password")
    audit_file = protected(
        "audit",
        json.dumps(
            [
                {
                    "db_user": "audit",
                    "db_password": "test-audit-password",
                    "user_id": str(user),
                    "project_id": str(project),
                }
            ]
        ),
    )
    security_file = protected(
        "statistics",
        json.dumps([{"user": "stats", "password": "test-stats-password", "project_id": str(project)}]),
    )
    data_file = protected("business", "test-business-password")
    business = ConnectionConfig.from_env(
        {
            "PAPERPILOT_DATA_HOST": "127.0.0.1",
            "PAPERPILOT_DATA_PORT": "5432",
            "PAPERPILOT_DATA_DBNAME": "test",
            "PAPERPILOT_DATA_USER": "business",
            "PAPERPILOT_DATA_PASSWORD_FILE": data_file,
        }
    )
    auth = auth_config_from_env(
        {
            "PAPERPILOT_AUTH_DB_ISOLATED_TEST": "1",
            "PAPERPILOT_AUTH_DB_HOST": "127.0.0.1",
            "PAPERPILOT_AUTH_DB_PORT": "5432",
            "PAPERPILOT_AUTH_DB_DBNAME": "test",
            "PAPERPILOT_AUTH_DB_USER": "auth",
            "PAPERPILOT_AUTH_DB_PASSWORD_FILE": auth_file,
        },
        business,
    )
    audit = scoped_audit_configurations(
        {
            "PAPERPILOT_SCOPED_AUDIT_ISOLATED_TEST": "1",
            "PAPERPILOT_SCOPED_AUDIT_HOST": "127.0.0.1",
            "PAPERPILOT_SCOPED_AUDIT_PORT": "5432",
            "PAPERPILOT_SCOPED_AUDIT_DBNAME": "test",
            "PAPERPILOT_SCOPED_AUDIT_OWNER": "owner",
            "PAPERPILOT_SCOPED_AUDIT_BINDING_OWNER": "binding_owner",
            "PAPERPILOT_SCOPED_AUDIT_BINDINGS_FILE": audit_file,
        },
        business,
    )
    stats = restricted_configurations(
        {
            "PAPERPILOT_SECURITY_ISOLATED_TEST": "1",
            "PAPERPILOT_SECURITY_DB_HOST": "127.0.0.1",
            "PAPERPILOT_SECURITY_DB_PORT": "5432",
            "PAPERPILOT_SECURITY_DB_DBNAME": "test",
            "PAPERPILOT_SECURITY_DB_VIEW_OWNER": "owner",
            "PAPERPILOT_SECURITY_DB_STATISTICS_SHA256": VIEW_SHA256["paper_statistics"],
            "PAPERPILOT_SECURITY_DB_TOPICS_SHA256": VIEW_SHA256["paper_topics"],
            "PAPERPILOT_SECURITY_DB_BINDINGS_FILE": security_file,
        }
    )
    assert business.user == "business" and auth.user == "auth"
    assert audit[0].project_id == stats[0].project_id == project
    assert all("password" not in repr(item) for item in (business, auth, audit[0], stats[0]))


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL semantics require Windows")
def test_windows_secret_file_checks_real_owner_dacl_and_write_sharing(tmp_path):
    import subprocess

    path = tmp_path / "secret.txt"
    path.write_text("windows-fixture\n", encoding="utf-8")
    script = r"""
      $ErrorActionPreference = 'Stop'
      $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
      $acl = [System.Security.AccessControl.FileSecurity]::new()
      $acl.SetOwner($user)
      $acl.SetAccessRuleProtection($true, $false)
      $rule = [System.Security.AccessControl.FileSystemAccessRule]::new($user, 'FullControl', 'Allow')
      $acl.AddAccessRule($rule)
      if ($env:PAPERPILOT_FIXTURE_EXTRA -eq '1') {
        $everyone = [System.Security.Principal.SecurityIdentifier]::new('S-1-1-0')
        $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($everyone, 'Read', 'Allow'))
      }
      [System.IO.File]::SetAccessControl($env:PAPERPILOT_FIXTURE_PATH, $acl)
    """

    def acl(extra):
        env = {**os.environ, "PAPERPILOT_FIXTURE_PATH": str(path), "PAPERPILOT_FIXTURE_EXTRA": str(extra)}
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env,
            capture_output=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")

    # A normal inherited DACL must never be accepted by the protected reader.
    with pytest.raises(RuntimeError):
        secret_value({"TEST_SECRET_FILE": str(path)}, "TEST_SECRET")
    acl(0)
    assert secret_value({"TEST_SECRET_FILE": str(path)}, "TEST_SECRET") == "windows-fixture"
    with path.open("r+b"):
        with pytest.raises(RuntimeError):
            secret_value({"TEST_SECRET_FILE": str(path)}, "TEST_SECRET")
    acl(1)
    with pytest.raises(RuntimeError):
        secret_value({"TEST_SECRET_FILE": str(path)}, "TEST_SECRET")
    acl(0)
    with pytest.raises(RuntimeError):
        secret_value({"TEST_SECRET_FILE": str(path)}, "TEST_SECRET", limit=2)
    from app.data.database import ConnectionConfig

    config = {"PAPERPILOT_DATA_DBNAME": "fixture", "PAPERPILOT_DATA_PASSWORD_FILE": str(path)}
    old = ConnectionConfig.from_env(config)
    path.write_text("rotated-fixture\n", encoding="utf-8")
    rotated = ConnectionConfig.from_env(config)
    assert old.password == "windows-fixture" and rotated.password == "rotated-fixture"
