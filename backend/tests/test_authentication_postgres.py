"""Authentication storage tests: isolated PostgreSQL, never a Mock session store."""

import secrets
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.data.authentication import PostgresSessions
from app.errors import AppError
from test_outbox_delivery import harness as postgres_harness  # noqa: F401 - shared isolated fixture

pytestmark = pytest.mark.real_database


@pytest.fixture
def harness(postgres_harness):  # noqa: F811 - pytest fixture injection
    return postgres_harness


def test_persistent_sessions_expiry_revocation_and_disable(harness):
    first = PostgresSessions(harness.database)
    password = secrets.token_urlsafe(24)
    first.initialize_password("alice", password)
    with pytest.raises(ValueError):
        first.initialize_password("alice", secrets.token_urlsafe(24))
    alice = first.authenticate("alice", password)
    assert alice is not None
    assert first.authenticate("alice", password + "x") is None
    assert first.authenticate("missing", password) is None
    token = first.create(alice, password)
    with harness.database.transaction() as conn:
        row = conn.execute("SELECT token_hash FROM auth_sessions WHERE user_id=%s", (alice,)).fetchone()
        assert token.encode() not in bytes(row["token_hash"])
    second = PostgresSessions(harness.database)
    assert second.current(token).user_id == alice
    second.revoke(token)
    with pytest.raises(AppError) as error:
        first.current(token)
    assert error.value.status == 401
    token = first.create(alice, password)
    with harness.database.transaction(write=True) as conn:
        conn.execute(
            "UPDATE auth_sessions SET last_seen_at=now()-interval '2 hours' WHERE user_id=%s", (alice,)
        )
    with pytest.raises(AppError):
        second.current(token, uuid4())
    token = first.create(alice, password)
    replacement = secrets.token_urlsafe(24)
    first.replace_password("alice", replacement)
    with pytest.raises(AppError):
        second.current(token)
    with pytest.raises(AppError):
        first.create(alice, password)
    assert first.authenticate("alice", password) is None
    assert first.authenticate("alice", replacement) == alice
    token = first.create(alice, replacement)
    with harness.database.transaction(write=True) as conn:
        conn.execute("UPDATE users SET disabled=true WHERE user_id=%s", (alice,))
    assert first.authenticate("alice", replacement) is None
    with pytest.raises(AppError):
        second.current(token, uuid4())
    with harness.database.transaction() as conn:
        events = conn.execute(
            "SELECT decision, execution_status FROM audit_events WHERE actor_id=%s "
            "AND action='auth.session.invalid'",
            (alice,),
        ).fetchall()
    assert len(events) == 2
    assert all(item == {"decision": "rejected", "execution_status": "completed"} for item in events)


def test_login_rate_limit_is_atomic_across_store_instances(harness):
    def attempt(_):
        store = PostgresSessions(harness.database, login_limit=3, login_window=60)
        try:
            store.check_login_rate("192.0.2.77")
            return "allowed"
        except AppError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(12)))
    assert outcomes.count("allowed") == 3
    assert outcomes.count("AUTH_RATE_LIMITED") == 9
