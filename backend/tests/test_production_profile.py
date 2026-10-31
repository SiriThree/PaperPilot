from dataclasses import replace
from pathlib import Path
import runpy

import pytest

from app.config import Settings


def configuration():
    return Settings(
        environment="production",
        production_security_profile=True,
        a_mode="persistent",
        c_mode="disabled",
        d_mode="real",
        auth_mode="credentials",
        auth_database="isolated",
        auth_lock_owner="pp_auth_lock_123456789abc",
        audit_trusted_owner="pp_audit_owner_123456789abc",
        audit_preflight="required",
        business_privilege_preflight="required",
        allowed_origins=("https://localhost:8443",),
    )


def test_explicit_local_production_profile_is_valid():
    configuration().validate()


@pytest.mark.parametrize(
    "change",
    [
        {"production_security_profile": False},
        {"auth_mode": "development"},
        {"auth_database": "shared"},
        {"auth_lock_owner": ""},
        {"audit_trusted_owner": ""},
        {"audit_preflight": "disabled"},
        {"business_privilege_preflight": "disabled"},
        {"c_mode": "mock"},
        {"d_mode": "mock"},
        {"security_experiments": True},
        {"allowed_origins": ("http://localhost:8443",)},
        {"allowed_origins": ("https://evil.invalid",)},
        {"allowed_origins": ("https://localhost:8443/",)},
        {"allowed_origins": ("https://user@localhost:8443",)},
    ],
)
def test_production_refuses_incomplete_or_unsafe_profiles(change):
    with pytest.raises((RuntimeError, ValueError)):
        replace(configuration(), **change).validate()


@pytest.mark.parametrize(
    "suffix,body",
    [
        ("/answers", {"question": "test"}),
        ("/report-drafts", {"question": "test"}),
        ("/search", {"query": "test"}),
        ("/papers/00000000-0000-0000-0000-000000000001/evidence", None),
    ],
)
def test_disabled_ai_returns_unavailable_before_enqueuing(signed, project, app, suffix, body):
    # Exercise route guards on the cheap fixture; actual production is tested over HTTPS separately.
    app.state.settings = replace(app.state.settings, c_mode="disabled")
    response = signed.post(f"/api/v1/projects/{project}" + suffix, json=body)
    assert response.status_code == 503 and response.json()["code"] == "AI_NOT_CONFIGURED"


def test_source_fingerprint_excludes_credentials_and_reports(tmp_path):
    tool = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/production_deployment.py"))
    root = tmp_path
    for folder in ("backend/app", "frontend/src", "deploy/production"):
        (root / folder).mkdir(parents=True)
    for name in ("Dockerfile.production", "compose.production.yaml", "source.py"):
        (root / name).write_text("source")
    fn = tool["fingerprint"]
    fn.__globals__["ROOT"] = root
    fn.__globals__["COMPOSE"] = root / "compose.production.yaml"
    fn.__globals__["__file__"] = str(root / "source.py")
    before = fn()
    (root / ".runtime").mkdir()
    (root / ".runtime/secret").write_text("not included")
    assert fn() == before
    (root / "backend/app/new.py").write_text("changed")
    assert fn() != before


def test_production_model_configuration_requires_https_and_fingerprints_changes():
    from app.production import SQLModel

    values = {"base_url": "https://model.invalid/v1", "api_key": "test-secret", "chat_model": "test-chat"}
    model = SQLModel.model_validate(values)
    assert "test-secret" not in repr(model) and "test-secret" not in model.model_dump_json()
    assert model.chat_settings().api_key == "test-secret"
    for field, value in (
        ("chat_model", "new-model"),
        ("api_key", "rotated-secret"),
        ("response_format", "json_object"),
    ):
        assert SQLModel.model_validate({**values, field: value}).fingerprint() != model.fingerprint()
    for address in ("http://127.0.0.1/v1", "https://user:secret@model.invalid/v1"):
        with pytest.raises(ValueError):
            SQLModel.model_validate({**values, "base_url": address})


def test_production_model_uses_explicit_composition_and_keeps_ast_rejection(monkeypatch):
    import asyncio
    from uuid import uuid4
    import httpx
    from app.adapters.mock_data import MockData, seed_id
    from app.adapters.ports import Context
    from app.ai.client import ModelClient
    from app.ai.settings import ChatSettings
    from app.ai.sql_generation import ModelSQLGenerator
    from app.security.adapter import build_security
    from app.security.sql_policy import SQL_BY_QUESTION

    monkeypatch.setenv("PAPERPILOT_ENV", "production")
    monkeypatch.delenv("PAPERPILOT_SQL_GENERATOR", raising=False)
    client = ModelClient(
        ChatSettings("https://model.invalid/v1", "test-secret", "test-model"),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": '{"sql":"DELETE FROM public.papers"}'},
                        }
                    ]
                },
            )
        ),
    )
    executed = []

    async def execute(ctx, plan):
        executed.append(plan)
        return [], []

    gateway = build_security(
        MockData(), query_executor=execute, candidate_generator=ModelSQLGenerator(client, SQL_BY_QUESTION)
    )
    result = asyncio.run(
        gateway.execute(Context(seed_id("alice"), seed_id("project-a"), uuid4()), "删除论文")
    )
    assert result.policy_id == "postgres-statistics-v2-model"
    assert result.reason.code == "SQL_POLICY_REJECTED" and not executed
    monkeypatch.setenv("PAPERPILOT_SQL_GENERATOR", "model")
    with pytest.raises(ValueError, match="one composition"):
        build_security(MockData(), candidate_generator=gateway._candidate_generator)
