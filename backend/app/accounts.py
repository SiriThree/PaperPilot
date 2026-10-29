"""Persistent password authentication. Runtime identity cannot create users or change credentials."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import re
import secrets
from uuid import UUID, uuid4

from app.auth import Session
from app.data.adapter import PostgresData, operation
from app.data.database import PostgresDatabase
from app.errors import AppError
from app.passwords import hash_password, verify_password
from app.schemas import AuditEvent


def verify_auth_login(connection, user):
    from psycopg.rows import tuple_row
    from app.security.executor import verify_login

    allowed = {
        ("paperpilot_auth", "accounts"): {"SELECT": None, "UPDATE": {"last_authenticated_at"}},
        ("paperpilot_auth", "sessions"): {"SELECT": None, "INSERT": None, "UPDATE": None, "DELETE": None},
        ("paperpilot_auth", "throttle"): {"SELECT": None, "INSERT": None, "UPDATE": None},
        ("paperpilot_auth", "schema_version"): {"SELECT": None},
        ("public", "users"): {"SELECT": {"user_id", "username"}},
    }
    with connection.cursor(row_factory=tuple_row) as cursor:
        verify_login(cursor, user)
        rows = cursor.execute(
            "SELECT n.nspname,c.relname,a.attname,"
            " has_column_privilege(c.oid,a.attnum,'SELECT'),has_column_privilege(c.oid,a.attnum,'INSERT'),"
            " has_column_privilege(c.oid,a.attnum,'UPDATE'),has_column_privilege(c.oid,a.attnum,'REFERENCES'),"
            " has_column_privilege(c.oid,a.attnum,'SELECT WITH GRANT OPTION,INSERT WITH GRANT OPTION,UPDATE WITH GRANT OPTION,REFERENCES WITH GRANT OPTION')"
            " FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid"
            " JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE a.attnum>0 AND NOT a.attisdropped"
            " AND c.relkind IN ('r','p','v','m','f') AND n.nspname !~ '^pg_' AND n.nspname<>'information_schema'"
        ).fetchall()
        seen = set()
        for schema, table, column, *rights in rows:
            permitted = allowed.get((schema, table), {})
            if rights[-1] or rights[3]:
                raise RuntimeError("Unsafe authentication grants")
            for index, privilege in enumerate(("SELECT", "INSERT", "UPDATE")):
                expected = privilege in permitted and (
                    permitted[privilege] is None or column in permitted[privilege]
                )
                if bool(rights[index]) != expected:
                    raise RuntimeError("Authentication grants differ from policy")
            seen.add((schema, table))
        if not set(allowed).issubset(seen):
            raise RuntimeError("Authentication schema unavailable")
        relations = cursor.execute(
            "SELECT n.nspname,c.relname,has_table_privilege(c.oid,'DELETE'),"
            " has_table_privilege(c.oid,'TRUNCATE,TRIGGER,REFERENCES,DELETE WITH GRANT OPTION')"
            " FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace"
            " WHERE c.relkind IN ('r','p','v','m','f') AND n.nspname !~ '^pg_' AND n.nspname<>'information_schema'"
        ).fetchall()
        if any(
            other or bool(delete) != ((schema, table) == ("paperpilot_auth", "sessions"))
            for schema, table, delete, other in relations
        ):
            raise RuntimeError("Unsafe authentication mutations")
        if cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace"
            " WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'"
            " AND CASE WHEN c.relkind='S' THEN has_sequence_privilege(c.oid,'USAGE,SELECT,UPDATE') ELSE false END)"
            " OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace"
            " WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'"
            " AND p.prosecdef AND has_function_privilege(p.oid,'EXECUTE'))"
        ).fetchone()[0]:
            raise RuntimeError("Unsafe authentication routines or sequences")
        if cursor.execute("SELECT version FROM paperpilot_auth.schema_version").fetchall() != [(1,)]:
            raise RuntimeError("Unsupported authentication schema")


@dataclass(frozen=True)
class Credential:
    user_id: UUID
    version: UUID


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PasswordSessions:
    def __init__(self, config):
        self.database = PostgresDatabase(config, pool_size=4)
        self.user = config.user
        self.dummy = hash_password("unusable timing placeholder for unknown accounts")
        try:
            with self.database.transaction() as conn:
                verify_auth_login(conn, self.user)
        except Exception:
            self.database.close()
            raise RuntimeError("Password authentication identity is not ready") from None

    def close(self):
        self.database.close()

    def authenticate(self, username, password, address):
        with self.database.transaction(write=True) as conn:
            verify_auth_login(conn, self.user)
            for key, limit in (("user:" + username.casefold(), 10), ("ip:" + address, 60)):
                attempts = conn.execute(
                    "INSERT INTO paperpilot_auth.throttle VALUES(%s,now(),1)"
                    " ON CONFLICT(key_hash) DO UPDATE SET attempts=CASE WHEN throttle.window_started_at<now()-interval '15 minutes'"
                    " THEN 1 ELSE throttle.attempts+1 END,window_started_at=CASE WHEN throttle.window_started_at<now()-interval '15 minutes'"
                    " THEN now() ELSE throttle.window_started_at END RETURNING attempts",
                    (_digest(key),),
                ).fetchone()["attempts"]
                if attempts > limit:
                    raise AppError(429, "LOGIN_RATE_LIMIT", "登录尝试过多，请稍后重试")
            row = conn.execute(
                "SELECT a.* FROM paperpilot_auth.accounts a JOIN public.users u USING(user_id) WHERE u.username=%s",
                (username.casefold(),),
            ).fetchone()
        valid = verify_password(password, row["password_hash"] if row else self.dummy)
        if not row or not valid or row["disabled"]:
            return None
        return Credential(row["user_id"], row["credential_version"])

    def create_authenticated(self, credential, old_token=None):
        token = secrets.token_urlsafe(32)
        with self.database.transaction(write=True) as conn:
            verify_auth_login(conn, self.user)
            row = conn.execute(
                "UPDATE paperpilot_auth.accounts SET last_authenticated_at=now()"
                " WHERE user_id=%s AND credential_version=%s AND NOT disabled RETURNING user_id",
                (credential.user_id, credential.version),
            ).fetchone()
            if row is None:
                raise AppError(401, "INVALID_CREDENTIALS", "账户名或密码不正确")
            if old_token:
                conn.execute(
                    "DELETE FROM paperpilot_auth.sessions WHERE token_hash=%s", (_digest(old_token),)
                )
            conn.execute(
                "INSERT INTO paperpilot_auth.sessions(token_hash,user_id,credential_version,expires_at) VALUES(%s,%s,%s,%s)",
                (
                    _digest(token),
                    credential.user_id,
                    credential.version,
                    datetime.now(UTC) + timedelta(hours=8),
                ),
            )
        return token

    def current(self, token):
        if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
            raise AppError(401, "UNAUTHENTICATED", "请先登录")
        with self.database.transaction(write=True) as conn:
            verify_auth_login(conn, self.user)
            row = conn.execute(
                "UPDATE paperpilot_auth.sessions s SET last_seen_at=now() FROM paperpilot_auth.accounts a"
                " WHERE s.user_id=a.user_id AND s.credential_version=a.credential_version AND NOT a.disabled"
                " AND s.token_hash=%s AND s.expires_at>now() AND s.last_seen_at>now()-interval '30 minutes'"
                " RETURNING s.user_id,s.expires_at",
                (_digest(token),),
            ).fetchone()
        if row is None:
            raise AppError(401, "UNAUTHENTICATED", "请先登录")
        return Session(row["user_id"], row["expires_at"])

    def revoke(self, token):
        if token:
            with self.database.transaction(write=True) as conn:
                verify_auth_login(conn, self.user)
                conn.execute("DELETE FROM paperpilot_auth.sessions WHERE token_hash=%s", (_digest(token),))


class AccountAdmin(PostgresData):
    def _account_audit(self, user_id, action):
        self._append_audit(
            AuditEvent(
                event_id=uuid4(),
                timestamp=datetime.now(UTC),
                request_id=uuid4(),
                actor_id=user_id,
                project_id=None,
                action=action,
                resource_type="account",
                decision="allowed",
                execution_status="completed",
                duration_ms=0,
                mock=False,
            )
        )

    @operation(write=True)
    def create_account(self, username, display_name, password):
        if re.fullmatch(r"[a-z][a-z0-9_.-]{2,63}", username) is None or not 1 <= len(display_name) <= 100:
            raise ValueError("Invalid account name")
        user_id = uuid4()
        encoded = hash_password(password)
        self._db.execute(
            "INSERT INTO public.users(user_id,username,display_name) VALUES(%s,%s,%s)",
            (user_id, username, display_name),
        )
        self._db.execute(
            "INSERT INTO paperpilot_auth.accounts(user_id,password_hash,credential_version) VALUES(%s,%s,%s)",
            (user_id, encoded, uuid4()),
        )
        self._account_audit(user_id, "auth.account.created")
        return user_id

    @operation(write=True)
    def change_password(self, user_id, password):
        encoded = hash_password(password)
        if (
            self._db.execute(
                "UPDATE paperpilot_auth.accounts SET password_hash=%s,credential_version=%s,updated_at=now() WHERE user_id=%s",
                (encoded, uuid4(), user_id),
            ).rowcount
            != 1
        ):
            raise ValueError("Unknown account")
        self._db.execute("DELETE FROM paperpilot_auth.sessions WHERE user_id=%s", (user_id,))
        self._account_audit(user_id, "auth.password.changed")

    @operation(write=True)
    def disable_account(self, user_id):
        if (
            self._db.execute(
                "UPDATE paperpilot_auth.accounts SET disabled=true,credential_version=%s,updated_at=now() WHERE user_id=%s",
                (uuid4(), user_id),
            ).rowcount
            != 1
        ):
            raise ValueError("Unknown account")
        self._db.execute("DELETE FROM paperpilot_auth.sessions WHERE user_id=%s", (user_id,))
        self._account_audit(user_id, "auth.account.disabled")
