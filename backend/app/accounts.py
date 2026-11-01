"""Operator account administration for B credentials; credentials and audit commit together."""

from datetime import UTC, datetime
import re
from uuid import uuid4

from argon2 import PasswordHasher
from app.data.adapter import PostgresData, operation
from app.schemas import AuditEvent


def _password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError("Password must contain between 12 and 1024 characters")
    return PasswordHasher().hash(password)


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
        encoded = _password_hash(password)
        self._db.execute(
            "INSERT INTO public.users(user_id,username,display_name,password_hash) VALUES(%s,%s,%s,%s)",
            (user_id, username, display_name, encoded),
        )
        self._account_audit(user_id, "auth.account.created")
        return user_id

    @operation(write=True)
    def change_password(self, user_id, password):
        encoded = _password_hash(password)
        if (
            self._db.execute(
                "UPDATE public.users SET password_hash=%s WHERE user_id=%s",
                (encoded, user_id),
            ).rowcount
            != 1
        ):
            raise ValueError("Unknown account")
        self._db.execute(
            "UPDATE public.auth_sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL",
            (user_id,),
        )
        self._account_audit(user_id, "auth.password.changed")

    @operation(write=True)
    def disable_account(self, user_id):
        if (
            self._db.execute(
                "UPDATE public.users SET disabled=true WHERE user_id=%s",
                (user_id,),
            ).rowcount
            != 1
        ):
            raise ValueError("Unknown account")
        self._db.execute(
            "UPDATE public.auth_sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL",
            (user_id,),
        )
        self._account_audit(user_id, "auth.account.disabled")
