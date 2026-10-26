"""SQLite lifecycle and transactions; no credential-bearing URL or schema reset."""

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.errors import AppError

MIGRATIONS = Path(__file__).parent / "migrations"


class SQLiteDatabase:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()

    def connect(self):
        # mode=rw prevents a misspelled runtime path from silently creating an empty database.
        connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def transaction(self, write=False):
        conn = None
        committing = False
        try:
            conn = self.connect()
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            committing = True
            conn.commit()
        except sqlite3.Error as exc:
            rollback_known = not committing
            if conn is not None and conn.in_transaction:
                conn.rollback()
                rollback_known = True
            if isinstance(exc, sqlite3.IntegrityError):
                raise AppError(409, "IDENTIFIER_CONFLICT", "数据完整性约束冲突") from None
            code = "DATA_UNAVAILABLE" if rollback_known else "DATA_COMMIT_UNKNOWN"
            raise AppError(503, code, "数据存储暂不可用或提交状态待确认") from None
        except Exception:
            if conn is not None and conn.in_transaction:
                conn.rollback()
            raise
        finally:
            if conn is not None:
                conn.close()

    def migrate(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=5)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables and "schema_migrations" not in tables:
                    raise RuntimeError("Unrecognized database; migration refused")
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("BEGIN IMMEDIATE")
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables and "schema_migrations" not in tables:
                    raise RuntimeError("Unrecognized database; migration refused")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
                )
                applied = dict(conn.execute("SELECT version,checksum FROM schema_migrations"))
                files = sorted(MIGRATIONS.glob("[0-9]*.sql"))
                if not files:
                    raise RuntimeError("Migration sources unavailable")
                known = {int(path.name.split("_")[0]) for path in files}
                if set(applied) - known:
                    raise RuntimeError("Unsupported database schema version")
                for file in files:
                    version = int(file.name.split("_")[0])
                    source = file.read_text(encoding="utf-8")
                    checksum = hashlib.sha256(source.encode()).hexdigest()
                    if version in applied:
                        if applied[version] != checksum:
                            raise RuntimeError("Applied migration checksum mismatch")
                        continue
                    statement = ""
                    for line in source.splitlines(keepends=True):
                        statement += line
                        if sqlite3.complete_statement(statement):
                            conn.execute(statement)
                            statement = ""
                    if statement.strip():
                        raise RuntimeError("Incomplete migration statement")
                    conn.execute(
                        "INSERT INTO schema_migrations VALUES(?,?,?)",
                        (version, checksum, datetime.now(UTC).isoformat()),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        except (sqlite3.Error, OSError):
            raise RuntimeError("SQLite connection or migration failed; no Mock fallback") from None

    def ready(self):
        with self.transaction() as conn:
            version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            if version != 1 or conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise AppError(503, "DATA_UNAVAILABLE", "数据库结构未就绪")
        return True

    def seed_development_users(self):
        with self.transaction(write=True) as conn:
            for name in ("alice", "bob", "eve"):
                user_id = str(uuid5(NAMESPACE_URL, f"paperpilot/development/{name}"))
                conn.execute("INSERT OR IGNORE INTO users VALUES(?,?,?)", (user_id, name, name.title()))
