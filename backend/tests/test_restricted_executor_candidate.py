"""Candidate guard tests; real authorization requires isolated PostgreSQL checks."""

import asyncio
from uuid import uuid4

import pytest
import psycopg

from app.adapters.ports import Context
from app.security.restricted_executor import (
    RestrictedConnection,
    RestrictedExecutor,
    _bounded_results,
    candidate_security_factory,
)
from app.security.sql_policy import QueryPlan, SQL_BY_QUESTION, prepare_query
from app.security.view_baseline import VIEW_SHA256


def setup_executor():
    ctx = Context(uuid4(), uuid4(), uuid4())
    executor = RestrictedExecutor(
        RestrictedConnection(
            ctx.project_id,
            "127.0.0.1",
            1,
            "fixture",
            "reader",
            "test",
            "owner",
            VIEW_SHA256["paper_statistics"],
            VIEW_SHA256["paper_topics"],
        )
    )
    return ctx, executor


def test_default_factory_refuses_candidate_without_preflight():
    _, executor = setup_executor()
    with pytest.raises(RuntimeError, match="readiness"):
        candidate_security_factory(executor)


def test_untrusted_runtime_digest_is_rejected():
    from dataclasses import replace

    _, executor = setup_executor()
    with pytest.raises(ValueError, match="configuration"):
        RestrictedExecutor(replace(executor.config, statistics_sha256="a" * 64))


def test_invalid_project_and_forged_query_are_rejected_before_connect(monkeypatch):
    ctx, executor = setup_executor()
    executor._ready = True

    async def forbidden():
        pytest.fail("invalid input must not connect")

    monkeypatch.setattr(executor, "_connect", forbidden)
    valid = prepare_query(next(iter(SQL_BY_QUESTION.values())), ctx.project_id)
    with pytest.raises(PermissionError):
        asyncio.run(executor(Context(ctx.user_id, uuid4(), ctx.request_id), valid))
    with pytest.raises(PermissionError):
        asyncio.run(executor(ctx, QueryPlan(sql="SELECT 1", parameters={"project_id": str(ctx.project_id)})))
    with pytest.raises(PermissionError):
        asyncio.run(executor(ctx, QueryPlan(sql=valid.sql, parameters={"project_id": str(uuid4())})))


def test_closed_executor_cannot_be_used_or_registered():
    ctx, executor = setup_executor()
    executor._ready = True
    executor.close()
    with pytest.raises(RuntimeError):
        candidate_security_factory(executor)
    with pytest.raises(RuntimeError):
        asyncio.run(executor(ctx, prepare_query(next(iter(SQL_BY_QUESTION.values())), ctx.project_id)))


def test_result_budgets_fail_closed():
    assert len(_bounded_results(["value"], [(i,) for i in range(99)])[1]) == 99
    with pytest.raises(ValueError, match="row budget"):
        _bounded_results(["value"], [(i,) for i in range(100)])
    with pytest.raises(ValueError, match="response budget"):
        _bounded_results(["value"], [("x" * (256 * 1024),)])
    with pytest.raises(ValueError, match="Unsupported result"):
        _bounded_results(["value"], [(object(),)])


def test_connection_failure_marks_executor_unhealthy(monkeypatch):
    ctx, executor = setup_executor()
    executor._ready = True

    async def unavailable():
        raise psycopg.OperationalError("fixture")

    monkeypatch.setattr(executor, "_connect", unavailable)
    with pytest.raises(psycopg.OperationalError):
        asyncio.run(executor(ctx, prepare_query(next(iter(SQL_BY_QUESTION.values())), ctx.project_id)))
    assert not executor._ready


def test_multiple_restricted_bindings_are_server_configured_and_unique(monkeypatch):
    import json
    from app.security.restricted_executor import restricted_configurations

    _, first = setup_executor()
    _, second = setup_executor()
    monkeypatch.setenv("PAPERPILOT_SECURITY_ISOLATED_TEST", "1")
    values = {
        "HOST": "127.0.0.1",
        "PORT": "1234",
        "DBNAME": "fixture",
        "VIEW_OWNER": "owner",
        "STATISTICS_SHA256": VIEW_SHA256["paper_statistics"],
        "TOPICS_SHA256": VIEW_SHA256["paper_topics"],
    }
    for key, value in values.items():
        monkeypatch.setenv("PAPERPILOT_SECURITY_DB_" + key, value)
    entries = [
        {"project_id": str(first.config.project_id), "user": "reader_a", "password": "secret_a"},
        {"project_id": str(second.config.project_id), "user": "reader_b", "password": "secret_b"},
    ]
    monkeypatch.setenv("PAPERPILOT_SECURITY_DB_BINDINGS", json.dumps(entries))
    configs = restricted_configurations(__import__("os").environ)
    assert [c.project_id for c in configs] == [first.config.project_id, second.config.project_id]
    assert "secret_a" not in repr(configs)
    entries[1]["user"] = entries[0]["user"]
    monkeypatch.setenv("PAPERPILOT_SECURITY_DB_BINDINGS", json.dumps(entries))
    with pytest.raises(RuntimeError, match="Invalid isolated"):
        restricted_configurations(__import__("os").environ)
    monkeypatch.setenv("PAPERPILOT_SECURITY_DB_BINDINGS", '[{"project_id":"a","project_id":"b"}]')
    with pytest.raises(RuntimeError, match="Invalid isolated"):
        restricted_configurations(__import__("os").environ)


def test_router_fails_closed_for_missing_project_and_closes(monkeypatch):
    from dataclasses import replace
    from app.security.restricted_executor import RestrictedExecutorRouter

    ctx, first = setup_executor()
    second_ctx = Context(ctx.user_id, uuid4(), uuid4())
    router = RestrictedExecutorRouter(
        [first.config, replace(first.config, project_id=second_ctx.project_id, user="reader_b")]
    )
    for child in router._executors.values():
        child._ready = True
    assert router._ready
    with pytest.raises(PermissionError, match="No restricted identity"):
        asyncio.run(router(Context(ctx.user_id, uuid4(), ctx.request_id), None))
    router._executors[second_ctx.project_id]._ready = False
    assert not router._ready
    router.close()
    with pytest.raises(PermissionError):
        asyncio.run(router(ctx, None))


def test_router_preflight_failure_closes_all_candidates(monkeypatch):
    from dataclasses import replace

    from app.security.restricted_executor import RestrictedExecutorRouter

    _, first = setup_executor()
    router = RestrictedExecutorRouter(
        [first.config, replace(first.config, project_id=uuid4(), user="reader_b")]
    )
    candidates = list(router._executors.values())

    async def okay():
        candidates[0]._ready = True
        return True

    async def failed():
        raise RuntimeError("preflight failure")

    monkeypatch.setattr(candidates[0], "ready", okay)
    monkeypatch.setattr(candidates[1], "ready", failed)
    with pytest.raises(RuntimeError, match="preflight failure"):
        asyncio.run(router.ready())
    assert router._closed and all(candidate._closed for candidate in candidates)
