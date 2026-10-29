"""Result and configuration boundaries; database behavior is tested separately on PostgreSQL."""

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.adapters.mock_data import MockData, seed_id
from app.adapters.ports import Context
from app.errors import AppError
from app.security import ReaderConfig, SecurityAdapter, build_security
from app.security.executor import MAX_RESPONSE_BYTES, QueryUnavailable, normalize_row


@pytest.mark.parametrize("value", [None, "text", 12, 1.5, True, uuid4()])
def test_driver_values_are_detached_json_scalars(value):
    assert normalize_row(["value"], [value]) == {"value": str(value) if isinstance(value, UUID) else value}


@pytest.mark.parametrize("value", [Decimal("1.2"), b"secret", object(), float("nan"), float("inf")])
def test_driver_values_outside_the_contract_fail(value):
    with pytest.raises(ValueError):
        normalize_row(["value"], [value])


def test_reader_repr_hides_password_and_configuration_is_required():
    config = ReaderConfig("127.0.0.1", 5432, "db", "reader", "PRIVATE_PASSWORD")
    assert "PRIVATE_PASSWORD" not in repr(config)
    for port in (True, 0, 65536):
        with pytest.raises(ValueError):
            ReaderConfig("127.0.0.1", port, "db", "reader", "secret")
    with pytest.raises(ValueError):
        ReaderConfig("127.0.0.1", 5432, "db", "reader", "")
    with pytest.raises(ValueError):
        build_security(MockData(), readers={})


@pytest.mark.parametrize("padding", [-1, 0, 1])
def test_complete_serialized_response_limit_includes_metadata(padding):
    data = MockData()
    ctx = Context(seed_id("alice"), seed_id("project-a"), uuid4())
    text = ""

    async def execute(*args):
        return ["title"], [{"title": text}]

    security = SecurityAdapter(data, query_executor=execute)
    empty = asyncio.run(security.execute(ctx, "按年份统计论文数量"))
    overhead = len(empty.model_dump_json().encode("utf-8"))
    text = "x" * (MAX_RESPONSE_BYTES - overhead + padding)
    result = asyncio.run(security.execute(ctx, "按年份统计论文数量"))
    if padding <= 0:
        assert result.execution_status == "executed"
        assert len(result.model_dump_json().encode("utf-8")) == MAX_RESPONSE_BYTES + padding
    else:
        assert result.reason.code == "QUERY_RESULT_TOO_LARGE" and not result.rows and not result.columns


def test_connection_failure_maps_to_safe_gateway_error_and_failed_audit():
    data = MockData()
    ctx = Context(seed_id("alice"), seed_id("project-a"), uuid4())

    async def unavailable(*args):
        raise QueryUnavailable("PRIVATE_PASSWORD")

    with pytest.raises(AppError) as error:
        asyncio.run(SecurityAdapter(data, query_executor=unavailable).execute(ctx, "按年份统计论文数量"))
    assert (error.value.status, error.value.code) == (503, "GATEWAY_UNAVAILABLE")
    assert "PRIVATE" not in error.value.message
    assert [event.execution_status for event in data.events] == ["pending", "failed"]
