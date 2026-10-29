"""Commit acknowledgement classification uses controlled connection failures."""

import psycopg
import pytest

from app.data.database import ConnectionConfig, PostgresDatabase
from app.errors import AppError


class ConnectionStub:
    def __init__(self, *, failure_at, error):
        self.failure_at = failure_at
        self.error = error
        self.rolled_back = False

    def execute(self, _statement):
        if self.failure_at == "begin":
            raise self.error

    def commit(self):
        if self.failure_at == "commit":
            raise self.error

    def rollback(self):
        self.rolled_back = True


@pytest.mark.parametrize(
    "write,failure_at,error,expected,discard",
    [
        (True, "commit", psycopg.OperationalError("fixture failure"), "DATA_COMMIT_UNKNOWN", True),
        (True, "commit", RuntimeError("fixture failure"), "DATA_COMMIT_UNKNOWN", True),
        (False, "commit", psycopg.OperationalError("fixture failure"), "DATA_UNAVAILABLE", False),
        (True, "begin", psycopg.OperationalError("fixture failure"), "DATA_UNAVAILABLE", False),
        (True, "commit", psycopg.errors.UniqueViolation("fixture conflict"), "IDENTIFIER_CONFLICT", False),
    ],
)
def test_transaction_reports_commit_uncertainty(monkeypatch, write, failure_at, error, expected, discard):
    pool = PostgresDatabase(ConnectionConfig("localhost", 5432, "fixture", "fixture", "fixture"))
    connection = ConnectionStub(failure_at=failure_at, error=error)
    discarded, released = [], []
    monkeypatch.setattr(pool, "_acquire", lambda: connection)
    monkeypatch.setattr(pool, "_discard", lambda item: discarded.append(item))
    monkeypatch.setattr(pool, "_release", lambda item: released.append(item))
    with pytest.raises(AppError) as caught:
        with pool.transaction(write=write):
            pass
    assert caught.value.code == expected
    assert connection.rolled_back
    assert (discarded, released) == (([connection], []) if discard else ([], [connection]))
