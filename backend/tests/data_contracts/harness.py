"""Provider seam for shared tests. Tests use only DataPort; fixtures own fault injection."""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Callable, Literal

from app.adapters.ports import DataPort, Job
from app.schemas import User


@dataclass
class DataHarness:
    data: DataPort
    owner: User
    member: User
    outsider: User
    kind: Literal["mock", "database"]
    failing_commit: Callable[[str], AbstractContextManager]
    reopen: Callable[[], DataPort] | None = None
    expire_claim: Callable[[Job], None] | None = None


def build_mock_harness():
    from contextlib import contextmanager
    from app.adapters.mock_data import MockData
    from app.errors import AppError

    failure = {"operation": None}

    def before_commit(operation):
        if operation == failure["operation"]:
            raise AppError(503, "DATA_UNAVAILABLE", "数据存储暂不可用")

    @contextmanager
    def failing_commit(operation):
        failure["operation"] = operation
        try:
            yield
        finally:
            failure["operation"] = None

    data = MockData(before_commit=before_commit)
    return DataHarness(
        data,
        data.user_by_name("alice"),
        data.user_by_name("bob"),
        data.user_by_name("eve"),
        "mock",
        failing_commit,
    )
