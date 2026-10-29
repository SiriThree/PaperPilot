"""PostgreSQL-backed credentials and opaque, revocable sessions.

This store is deliberately separate from AB-1: business data adapters never act
as authentication providers. Database setup and initial passwords are explicit.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.errors import AppError
from app.adapters.data_rules import fingerprint
from app.schemas import AuditEvent


@dataclass(frozen=True)
class Session:
    user_id: UUID
    expires_at: datetime


class PostgresSessions:
    def __init__(
        self, database, *, idle_seconds=1800, absolute_seconds=28800, login_limit=10, login_window=300
    ):
        if not 60 <= idle_seconds <= absolute_seconds <= 604800:
            raise ValueError("Invalid session expiry policy")
        self.database = database
        self.idle_seconds = idle_seconds
        self.absolute_seconds = absolute_seconds
        self.login_limit = login_limit
        self.login_window = login_window
        self.hasher = PasswordHasher()
        self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))

    @staticmethod
    def _digest(token):
        if not isinstance(token, str) or not 32 <= len(token) <= 128 or not token.isascii():
            return None
        return hashlib.sha256(token.encode("ascii")).digest()

    def authenticate(self, username, password):
        if not isinstance(password, str) or len(password.encode("utf-8")) > 1024:
            return None
        with self.database.transaction() as conn:
            user = conn.execute(
                "SELECT user_id, password_hash, disabled FROM users WHERE username=%s", (username,)
            ).fetchone()
        candidate = user["password_hash"] if user and user["password_hash"] else self._dummy_hash
        try:
            valid = self.hasher.verify(candidate, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            valid = False
        if not user or user["disabled"] or not user["password_hash"] or not valid:
            return None
        return user["user_id"]

    def check_login_rate(self, peer, request_id=None):
        """Count all attempts using the ASGI peer, without parsing forwarded headers.

        A trusted proxy or ASGI server may rewrite that peer: deployments must
        configure proxy trust correctly before relying on this limit.
        """
        digest = hashlib.sha256(str(peer or "unknown").encode("utf-8")).digest()
        with self.database.transaction(write=True) as conn:
            row = conn.execute(
                "INSERT INTO auth_login_limits(peer_hash,window_start,attempts) VALUES(%s,now(),1) "
                "ON CONFLICT(peer_hash) DO UPDATE SET "
                "window_start=CASE WHEN auth_login_limits.window_start <= now()-(%s*interval '1 second') "
                "THEN now() ELSE auth_login_limits.window_start END, "
                "attempts=CASE WHEN auth_login_limits.window_start <= now()-(%s*interval '1 second') "
                "THEN 1 ELSE LEAST(auth_login_limits.attempts+1,%s) END RETURNING attempts",
                (digest, self.login_window, self.login_window, self.login_limit + 1),
            ).fetchone()
            if row["attempts"] > self.login_limit and request_id is not None:
                self._audit_completed(conn, None, request_id, "auth.login.rate_limited", "rejected")
        if row["attempts"] > self.login_limit:
            raise AppError(429, "AUTH_RATE_LIMITED", "登录请求过于频繁，请稍后重试")

    @staticmethod
    def _audit_completed(conn, user_id, request_id, action, decision="allowed"):
        event = AuditEvent(
            event_id=uuid4(),
            timestamp=datetime.now(UTC),
            request_id=request_id,
            actor_id=user_id,
            project_id=None,
            action=action,
            resource_type="session",
            decision=decision,
            execution_status="completed",
            duration_ms=0,
            mock=False,
        )
        try:
            conn.execute(
                "INSERT INTO audit_events(event_id,timestamp,request_id,actor_id,project_id,"
                "action,resource_type,decision,execution_status,policy_id,duration_ms,mock,event_fingerprint) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    event.event_id,
                    event.timestamp,
                    event.request_id,
                    event.actor_id,
                    event.project_id,
                    event.action,
                    event.resource_type,
                    event.decision,
                    event.execution_status,
                    event.policy_id,
                    event.duration_ms,
                    event.mock,
                    fingerprint(event),
                ),
            )
        except Exception:
            raise AppError(503, "AUDIT_UNAVAILABLE", "审计暂不可用，会话变更未确认") from None

    def create(self, user_id, password, request_id=None, old_token=None):
        token = secrets.token_urlsafe(32)
        with self.database.transaction(write=True) as conn:
            # Serialize with password reset so a credential verified just before a
            # reset cannot create a new valid session after reset has completed.
            lock = getattr(self.database, "lock_credential", None)
            user = (
                lock(conn, user_id)
                if callable(lock)
                else conn.execute(
                    "SELECT password_hash, disabled FROM users WHERE user_id=%s FOR UPDATE", (user_id,)
                ).fetchone()
            )
            if user is None or user["disabled"] or not user["password_hash"]:
                raise AppError(401, "UNAUTHENTICATED", "请先登录")
            try:
                self.hasher.verify(user["password_hash"], password)
            except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError):
                raise AppError(401, "UNAUTHENTICATED", "请先登录") from None
            result = conn.execute(
                "INSERT INTO auth_sessions(token_hash,user_id,expires_at) "
                "SELECT %s,user_id,now() + (%s * interval '1 second') FROM users "
                "WHERE user_id=%s AND NOT disabled AND password_hash IS NOT NULL RETURNING user_id",
                (self._digest(token), self.absolute_seconds, user_id),
            ).fetchone()
            if result is None:
                raise AppError(401, "UNAUTHENTICATED", "请先登录")
            old_digest = self._digest(old_token)
            if old_digest is not None:
                conn.execute(
                    "UPDATE auth_sessions SET revoked_at=now() WHERE token_hash=%s AND revoked_at IS NULL",
                    (old_digest,),
                )
            if request_id is not None:
                self._audit_completed(conn, user_id, request_id, "auth.login")
        return token

    def current(self, token, request_id=None):
        digest = self._digest(token)
        if digest is None:
            raise AppError(401, "UNAUTHENTICATED", "请先登录")
        with self.database.transaction(write=True) as conn:
            row = conn.execute(
                "UPDATE auth_sessions s SET last_seen_at=now() FROM users u "
                "WHERE s.token_hash=%s AND s.user_id=u.user_id AND NOT u.disabled "
                "AND s.revoked_at IS NULL AND s.expires_at>now() "
                "AND s.last_seen_at > now() - (%s * interval '1 second') "
                "RETURNING s.user_id,s.expires_at",
                (digest, self.idle_seconds),
            ).fetchone()
            if row is None and request_id is not None:
                known = conn.execute(
                    "SELECT user_id FROM auth_sessions WHERE token_hash=%s", (digest,)
                ).fetchone()
                if known:
                    self._audit_completed(
                        conn, known["user_id"], request_id, "auth.session.invalid", "rejected"
                    )
        if row is None:
            raise AppError(401, "UNAUTHENTICATED", "请先登录")
        return Session(user_id=row["user_id"], expires_at=row["expires_at"])

    def revoke(self, token, request_id=None, user_id=None):
        digest = self._digest(token)
        if digest is not None:
            with self.database.transaction(write=True) as conn:
                conn.execute(
                    "UPDATE auth_sessions SET revoked_at=now() WHERE token_hash=%s AND revoked_at IS NULL",
                    (digest,),
                )
                if request_id is not None:
                    self._audit_completed(conn, user_id, request_id, "auth.logout")

    def initialize_password(self, username, password):
        """One-time explicit operator action. Never overwrites an existing hash."""
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            raise ValueError("Password must contain between 12 and 1024 characters")
        password_hash = self.hasher.hash(password)
        with self.database.transaction(write=True) as conn:
            result = conn.execute(
                "UPDATE users SET password_hash=%s WHERE username=%s AND password_hash IS NULL "
                "RETURNING user_id",
                (password_hash, username),
            ).fetchone()
            if result is None:
                raise ValueError("User missing or password already initialized")

    def replace_password(self, username, password):
        """Operator-only password reset, atomically revoking all prior sessions."""
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            raise ValueError("Password must contain between 12 and 1024 characters")
        password_hash = self.hasher.hash(password)
        with self.database.transaction(write=True) as conn:
            user = conn.execute(
                "UPDATE users SET password_hash=%s WHERE username=%s RETURNING user_id",
                (password_hash, username),
            ).fetchone()
            if user is None:
                raise ValueError("User missing")
            conn.execute(
                "UPDATE auth_sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL",
                (user["user_id"],),
            )
