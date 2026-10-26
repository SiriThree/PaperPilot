"""D result/authorization contracts, never a claim of real SQL attack protection."""

import asyncio

import pytest

from app.errors import AppError
from app.schemas import SecureQueryResult


def result(port, ctx, question):
    return SecureQueryResult.model_validate(asyncio.run(port.execute(ctx, question)))


def test_owner_authorization_returns_none(security_harness):
    h = security_harness
    assert h.security.authorize(h.ctx, "audit.read", "owner") is None


@pytest.mark.parametrize("action", ["members.manage", "paper.remove", "audit.read"])
def test_owner_only_action_rejects_member(security_harness, action):
    with pytest.raises(AppError) as error:
        security_harness.security.authorize(security_harness.member_ctx, action, "member")
    assert error.value.status == 403


def test_success_correlates_request_and_separates_execution(security_harness):
    h = security_harness
    response = result(h.security, h.ctx, h.normal_question)
    assert (response.decision, response.execution_status) == ("allowed", "executed")
    assert response.request_id == h.ctx.request_id and response.policy_id
    assert response.mock is h.is_mock
    assert response.columns and all(set(row) <= set(response.columns) for row in response.rows)


def test_rejection_has_reason_without_results(security_harness):
    h = security_harness
    response = result(h.security, h.ctx, h.rejected_question)
    assert (response.decision, response.execution_status) == ("rejected", "not_executed")
    assert response.reason and not response.rows and not response.columns
    assert response.request_id == h.ctx.request_id


def test_execution_failure_is_not_success(security_harness):
    h = security_harness
    response = result(h.failing_security, h.ctx, h.failure_question)
    assert (response.decision, response.execution_status) == ("undetermined", "failed")
    assert response.reason and not response.rows and not response.columns
    assert response.request_id == h.ctx.request_id


def test_unavailable_gateway_raises_safe_error(security_harness):
    h = security_harness
    with pytest.raises(AppError) as error:
        result(h.unavailable_security, h.ctx, h.unavailable_question)
    assert error.value.status == 503 and error.value.code == "GATEWAY_UNAVAILABLE"
