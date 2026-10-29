"""PostgreSQL 16 connection lifecycle, migrations and AB-1 error mapping.

Credentials never carry defaults; configuration comes from PAPERPILOT_DATA_* variables
or an explicit ConnectionConfig. Readiness and migrations stay outside the DataPort.
"""

import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import psycopg
from psycopg.rows import dict_row

from app.errors import AppError

MIGRATIONS = Path(__file__).parent / "migrations"
EXPECTED_VERSION = 3


@dataclass(frozen=True)
class ConnectionConfig:
    host: str
    port: int
    user: str
    password: str
    dbname: str
    connect_timeout: int = 10

    def conninfo(self):
        return dict(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=self.dbname,
            connect_timeout=self.connect_timeout,
        )

    @classmethod
    def from_env(cls, env=None):
        import os

        environ = os.environ if env is None else env
        from app.secrets import read_secret

        try:
            password = read_secret("PAPERPILOT_DATA_PASSWORD", environ)
        except ValueError:
            password = ""
        dbname = environ.get("PAPERPILOT_DATA_DBNAME", "")
        if not password or not dbname:
            raise RuntimeError(
                "PAPERPILOT_DATA_PASSWORD and PAPERPILOT_DATA_DBNAME must be provided explicitly"
            )
        return cls(
            host=environ.get("PAPERPILOT_DATA_HOST", "127.0.0.1"),
            port=int(environ.get("PAPERPILOT_DATA_PORT", "54326")),
            user=environ.get("PAPERPILOT_DATA_USER", "postgres"),
            password=password,
            dbname=dbname,
        )


class PostgresDatabase:
    """Small fixed pool; every DataPort call owns one connection and one transaction."""

    def __init__(self, config: ConnectionConfig, *, pool_size: int = 8):
        if pool_size < 1:
            raise ValueError("pool_size must be positive")
        self.config = config
        self._condition = threading.Condition()
        self._pool = []
        self._issued = 0
        self._pool_size = pool_size
        self._closed = False

    def _acquire(self):
        with self._condition:
            while not self._pool and self._issued >= self._pool_size:
                if self._closed:
                    raise AppError(503, "DATA_UNAVAILABLE", "数据存储已关闭")
                self._condition.wait(timeout=5)
            if self._pool:
                return self._pool.pop()
            self._issued += 1
        return self._connect()

    def _connect(self):
        try:
            return psycopg.connect(row_factory=dict_row, **self.config.conninfo())
        except psycopg.Error:
            with self._condition:
                self._issued -= 1
                self._condition.notify_all()
            raise AppError(503, "DATA_UNAVAILABLE", "数据存储暂不可用") from None

    def _release(self, connection):
        with self._condition:
            if self._closed:
                self._issued -= 1
                connection.close()
                self._condition.notify_all()
                return
            self._pool.append(connection)
            self._condition.notify_all()

    def _discard(self, connection):
        """Drop a broken connection from the pool instead of reusing it."""
        with self._condition:
            self._issued -= 1
        try:
            connection.close()
        except psycopg.Error:
            pass

    def close(self):
        with self._condition:
            self._closed = True
            idle, self._pool = self._pool, []
            for connection in idle:
                try:
                    connection.close()
                except psycopg.Error:
                    pass

    @contextmanager
    def transaction(self, write=False):
        connection = self._acquire()
        broken = False
        try:
            try:
                connection.execute("BEGIN")
                yield connection
                connection.commit()
            except psycopg.errors.IntegrityError as error:
                # Constraint violations are definite rollbacks, including deferred ones at COMMIT.
                broken = not _safe_rollback(connection)
                raise AppError(409, "IDENTIFIER_CONFLICT", "数据完整性约束冲突") from error
            except psycopg.Error as error:
                known = _safe_rollback(connection)
                broken = not known
                if known:
                    raise AppError(503, "DATA_UNAVAILABLE", "数据存储暂不可用") from error
                raise AppError(503, "DATA_COMMIT_UNKNOWN", "提交结果待确认") from error
            except Exception:
                broken = not _safe_rollback(connection)
                raise
        finally:
            if broken:
                self._discard(connection)
            else:
                self._release(connection)

    def migrate(self):
        files = sorted(MIGRATIONS.glob("[0-9]*.sql"))
        if not files:
            raise RuntimeError("Migration sources unavailable")
        connection = self._connect_admin()
        try:
            connection.execute("BEGIN")
            tables = {
                row["tablename"]
                for row in connection.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                ).fetchall()
            }
            # A foreign non-empty database is refused; an already-migrated one re-verifies checksums.
            if tables and "schema_migrations" not in tables:
                raise RuntimeError("Unrecognized database; migration refused")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations("
                "version integer PRIMARY KEY, checksum varchar(64) NOT NULL, applied_at timestamptz NOT NULL)"
            )
            applied = {
                row["version"]: row["checksum"]
                for row in connection.execute("SELECT version, checksum FROM schema_migrations").fetchall()
            }
            known = {int(path.name.split("_")[0]) for path in files}
            if set(applied) - known:
                raise RuntimeError("Unsupported database schema version")
            for path in files:
                version = int(path.name.split("_")[0])
                source = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(source.encode()).hexdigest()
                if version in applied:
                    if applied[version] != checksum:
                        raise RuntimeError("Applied migration checksum mismatch")
                    continue
                connection.execute(source)
                connection.execute(
                    "INSERT INTO schema_migrations(version, checksum, applied_at) VALUES(%s,%s,%s)",
                    (version, checksum, datetime.now(UTC)),
                )
            connection.commit()
        except Exception:
            _safe_rollback(connection)
            raise
        finally:
            connection.close()

    def _connect_admin(self):
        try:
            return psycopg.connect(row_factory=dict_row, autocommit=False, **self.config.conninfo())
        except psycopg.Error:
            raise RuntimeError("PostgreSQL connection or migration failed; no Mock fallback") from None

    def ready(self):
        try:
            with self.transaction() as connection:
                row = connection.execute("SELECT max(version) AS version FROM schema_migrations").fetchone()
                if row is None or row["version"] != EXPECTED_VERSION:
                    raise AppError(503, "DATA_UNAVAILABLE", "数据库结构未就绪")
        except RuntimeError:
            raise
        except AppError:
            raise
        except Exception:
            raise AppError(503, "DATA_UNAVAILABLE", "数据存储暂不可用") from None
        return True

    def seed_development_users(self):
        """Deterministic development identities; additive on repeated runs."""
        with self.transaction(write=True) as connection:
            for name in ("alice", "bob", "eve"):
                connection.execute(
                    "INSERT INTO users(user_id, username, display_name) VALUES(%s,%s,%s)"
                    " ON CONFLICT (user_id) DO NOTHING",
                    (
                        str(uuid5(NAMESPACE_URL, f"paperpilot/development/{name}")),
                        name,
                        name.title(),
                    ),
                )


def _safe_rollback(connection):
    try:
        connection.rollback()
        return True
    except psycopg.Error:
        return False
