"""Pool lifecycle checks use a fake connection; no database is contacted."""

import threading

import pytest

from app.data.database import ConnectionConfig, PostgresDatabase
from app.errors import AppError


class FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def database():
    return PostgresDatabase(ConnectionConfig("localhost", 5432, "test", "test", "test"), pool_size=1)


def test_closed_pool_refuses_new_connections(monkeypatch):
    pool = database()
    opened = []
    monkeypatch.setattr(pool, "_connect", lambda: opened.append(FakeConnection()))
    pool.close()
    with pytest.raises(AppError, match="数据存储已关闭"):
        pool._acquire()
    assert opened == []


def test_close_wakes_waiting_acquirer(monkeypatch):
    pool = database()
    connection = FakeConnection()
    monkeypatch.setattr(pool, "_connect", lambda: connection)
    assert pool._acquire() is connection
    started = threading.Event()
    errors = []

    def acquire():
        started.set()
        try:
            pool._acquire()
        except AppError as error:
            errors.append(error.code)

    worker = threading.Thread(target=acquire)
    worker.start()
    assert started.wait(1)
    pool.close()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert errors == ["DATA_UNAVAILABLE"]
    pool._release(connection)
    assert connection.closed


def test_connection_finishing_after_shutdown_is_closed(monkeypatch):
    pool = database()
    started = threading.Event()
    finish = threading.Event()
    connection = FakeConnection()
    errors = []

    def connect():
        started.set()
        assert finish.wait(2)
        return connection

    monkeypatch.setattr(pool, "_connect", connect)

    def acquire():
        try:
            pool._acquire()
        except AppError as error:
            errors.append(error.code)

    worker = threading.Thread(target=acquire)
    worker.start()
    assert started.wait(1)
    pool.close()
    finish.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert connection.closed
    assert errors == ["DATA_UNAVAILABLE"]
