"""Configuration checks use no database and do not prove database security."""

import json
from dataclasses import replace
from uuid import uuid4

import pytest

from app.config import Settings
from app.main import create_app
from app.data.database import ConnectionConfig
from app.data.scoped_audit import ScopedAuditRouter, scoped_audit_configurations


def test_scoped_audit_cannot_use_development_identity_or_production():
    with pytest.raises(RuntimeError, match="credential authentication"):
        Settings(environment="test", a_mode="persistent", scoped_audit="isolated").validate()
    with pytest.raises(RuntimeError, match="isolated test mode"):
        Settings(
            environment="production", a_mode="persistent", auth_mode="credentials", scoped_audit="isolated"
        ).validate()


def test_required_scoped_reader_registration_never_falls_back():
    settings = Settings(
        environment="test", a_mode="persistent", auth_mode="credentials", scoped_audit="isolated"
    )
    with pytest.raises(RuntimeError, match="registration do not match"):
        create_app(settings)
    with pytest.raises(RuntimeError, match="registration do not match"):
        create_app(Settings(environment="test"), scoped_audit_reader=object())


def test_composition_rejects_scoped_mode_without_business_preflight(monkeypatch):
    from app.adapters.data_binding import DataBinding
    from app.data import binding
    from app.dev_composition import create_app as create_composed

    closed = []
    monkeypatch.setenv("PAPERPILOT_DATA_DBNAME", "test")
    monkeypatch.setenv("PAPERPILOT_DATA_PASSWORD", "fixture-only")
    monkeypatch.setattr(
        binding,
        "compose_data_binding",
        lambda *_: DataBinding(object(), lambda: True, lambda: closed.append(True)),
    )
    settings = Settings(
        environment="test", a_mode="persistent", auth_mode="credentials", scoped_audit="isolated"
    )
    with pytest.raises(RuntimeError, match="requires enforced business audit preflight"):
        create_composed(settings)
    assert closed == [True]


def test_multiple_bindings_parse_without_exposing_secrets():
    user_a, user_b, project_a, project_b = [str(uuid4()) for _ in range(4)]
    entries = [
        {
            "db_user": "reader_a",
            "db_password": "fixture-secret-a",
            "user_id": user_a,
            "project_id": project_a,
        },
        {
            "db_user": "reader_b",
            "db_password": "fixture-secret-b",
            "user_id": user_b,
            "project_id": project_b,
        },
    ]
    base = {
        "PAPERPILOT_SCOPED_AUDIT_ISOLATED_TEST": "1",
        "PAPERPILOT_SCOPED_AUDIT_HOST": "127.0.0.1",
        "PAPERPILOT_SCOPED_AUDIT_PORT": "5432",
        "PAPERPILOT_SCOPED_AUDIT_DBNAME": "isolated",
        "PAPERPILOT_SCOPED_AUDIT_OWNER": "owner",
        "PAPERPILOT_SCOPED_AUDIT_BINDING_OWNER": "binder",
        "PAPERPILOT_SCOPED_AUDIT_BINDINGS": json.dumps(entries),
    }
    connection = ConnectionConfig("127.0.0.1", 5432, "business", "fixture-secret-data", "isolated")
    configs = scoped_audit_configurations(base, connection)
    assert [config.user for config in configs] == ["reader_a", "reader_b"]
    assert all("fixture-secret" not in repr(config) for config in configs)
    with pytest.raises(ValueError, match="trusted database boundary"):
        ScopedAuditRouter([configs[0], replace(configs[1], port=5433)])
    for bad in (
        [entries[0], entries[0]],
        [entries[0], {**entries[1], "db_user": "reader_a"}],
        [entries[0], {**entries[1], "db_user": "business"}],
        [entries[0], {**entries[1], "extra": "bad"}],
    ):
        with pytest.raises(RuntimeError) as error:
            scoped_audit_configurations(
                {**base, "PAPERPILOT_SCOPED_AUDIT_BINDINGS": json.dumps(bad)}, connection
            )
        assert "fixture-secret" not in str(error.value)
    with pytest.raises(RuntimeError):
        scoped_audit_configurations({**base, "PAPERPILOT_SCOPED_AUDIT_USER": "old"}, connection)
    with pytest.raises(RuntimeError):
        scoped_audit_configurations(
            {**base, "PAPERPILOT_SCOPED_AUDIT_BINDINGS": '[{"db_user":"a","db_user":"b"}]'}, connection
        )
