"""Real password/session storage and least-privilege failures; no external identity service."""

from dataclasses import replace
import hashlib
import os
import secrets
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.accounts import AccountAdmin, PasswordSessions
from app.adapters.data_binding import DataBinding
from app.config import Settings
from app.errors import AppError
from app.main import create_app
from app.security.provision import create_login, install, install_password_auth
from security_contracts.postgres_provider import postgres_workspace

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        os.getenv("PAPERPILOT_SECURITY_CURRENT_SOURCE") != "1",
        reason="Requires explicit disposable PostgreSQL",
    ),
]


@pytest.fixture(scope="module")
def pg():
    with postgres_workspace() as pg:
        role, password = "pp_auth_" + uuid4().hex, secrets.token_urlsafe(32)
        with psycopg.connect(**pg.config.conninfo(), autocommit=True) as admin:
            install(admin, existing_views=True)
            install_password_auth(admin)
            create_login(admin, role, password, auth=True)
        pg.auth_config = replace(pg.config, user=role, password=password)
        yield pg


@pytest.fixture
def state(pg):
    admin = AccountAdmin(pg.data.database, pg.data.settings)
    username, password = "user_" + uuid4().hex[:12], secrets.token_urlsafe(24)
    user_id = admin.create_account(username, "Password fixture", password)
    store = PasswordSessions(pg.auth_config)
    try:
        yield SimpleNamespace(
            pg=pg, admin=admin, username=username, password=password, user_id=user_id, store=store
        )
    finally:
        store.close()


def credential(state):
    return state.store.authenticate(state.username, state.password, uuid4().hex)


def test_password_hash_and_hashed_persistent_session(state):
    auth = credential(state)
    assert auth and auth.user_id == state.user_id
    token = state.store.create_authenticated(auth)
    with state.pg.data.database.transaction() as conn:
        encoded = conn.execute(
            "SELECT password_hash FROM paperpilot_auth.accounts WHERE user_id=%s", (state.user_id,)
        ).fetchone()["password_hash"]
        assert encoded.startswith("scrypt-v1$") and state.password not in encoded
        saved = conn.execute(
            "SELECT token_hash FROM paperpilot_auth.sessions WHERE user_id=%s", (state.user_id,)
        ).fetchone()["token_hash"]
        assert saved == hashlib.sha256(token.encode()).hexdigest() and token not in saved
    state.store.close()
    reopened = PasswordSessions(state.pg.auth_config)
    try:
        assert reopened.current(token).user_id == state.user_id
        reopened.revoke(token)
        with pytest.raises(AppError) as ended:
            reopened.current(token)
        assert ended.value.status == 401
    finally:
        reopened.close()


def test_wrong_unknown_and_missing_password_never_authenticate(state):
    for username, password in (
        (state.username, "wrong"),
        ("unknown_" + uuid4().hex, "wrong"),
        (state.username, ""),
    ):
        assert state.store.authenticate(username, password, uuid4().hex) is None


def test_password_rotation_invalidates_sessions_and_inflight_credentials(state):
    auth = credential(state)
    token = state.store.create_authenticated(auth)
    new_password = secrets.token_urlsafe(24)
    state.admin.change_password(state.user_id, new_password)
    with pytest.raises(AppError):
        state.store.current(token)
    with pytest.raises(AppError):
        state.store.create_authenticated(auth)
    assert state.store.authenticate(state.username, state.password, uuid4().hex) is None
    assert state.store.authenticate(state.username, new_password, uuid4().hex)


def test_disabling_account_revokes_existing_sessions(state):
    token = state.store.create_authenticated(credential(state))
    state.admin.disable_account(state.user_id)
    assert credential(state) is None
    with pytest.raises(AppError):
        state.store.current(token)


@pytest.mark.parametrize("expired", ["idle", "absolute"])
def test_idle_and_absolute_expiration(state, expired):
    token = state.store.create_authenticated(credential(state))
    with state.pg.data.database.transaction(write=True) as conn:
        if expired == "idle":
            conn.execute(
                "UPDATE paperpilot_auth.sessions SET last_seen_at=now()-interval '31 minutes' WHERE user_id=%s",
                (state.user_id,),
            )
        else:
            conn.execute(
                "UPDATE paperpilot_auth.sessions SET created_at=now()-interval '10 hours',expires_at=now()-interval '1 hour' WHERE user_id=%s",
                (state.user_id,),
            )
    with pytest.raises(AppError):
        state.store.current(token)


def test_login_rate_budget_is_persistent(state):
    address = uuid4().hex
    for _ in range(10):
        assert state.store.authenticate(state.username, "bad", address) is None
    with pytest.raises(AppError) as limited:
        state.store.authenticate(state.username, state.password, address)
    assert limited.value.status == 429


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM public.chunks",
        "SELECT * FROM public.audit_events",
        "UPDATE paperpilot_auth.accounts SET disabled=false",
        "DELETE FROM public.audit_events",
        "CREATE TABLE public.auth_unsafe(x int)",
    ],
)
def test_auth_login_cannot_read_business_or_reset_credentials(state, statement):
    with psycopg.connect(**state.pg.auth_config.conninfo(), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)


@pytest.mark.parametrize("drift", ["column", "sequence", "routine"])
def test_authentication_rechecks_acl_drift_before_session_lookup(state, drift):
    from psycopg import sql

    token = state.store.create_authenticated(credential(state))
    with psycopg.connect(**state.pg.config.conninfo(), autocommit=True) as admin:
        role = sql.Identifier(state.pg.auth_config.user)
        if drift == "column":
            grant = sql.SQL("GRANT UPDATE(disabled) ON paperpilot_auth.accounts TO {}").format(role)
            undo = sql.SQL("REVOKE UPDATE(disabled) ON paperpilot_auth.accounts FROM {}").format(role)
        elif drift == "sequence":
            admin.execute("CREATE SEQUENCE public.auth_acl_sequence")
            grant = sql.SQL("GRANT USAGE ON public.auth_acl_sequence TO {}").format(role)
            undo = sql.SQL("DROP SEQUENCE public.auth_acl_sequence")
        else:
            grant = sql.SQL(
                "CREATE FUNCTION public.auth_acl_routine() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'"
            )
            undo = sql.SQL("DROP FUNCTION public.auth_acl_routine()")
        try:
            admin.execute(grant)
            with pytest.raises(RuntimeError):
                state.store.current(token)
        finally:
            admin.execute(undo)
    assert state.store.current(token).user_id == state.user_id


def test_operator_account_creation_and_audit_are_atomic(state, monkeypatch):
    username = "atomic_" + uuid4().hex[:12]

    def fail(event):
        raise RuntimeError("private audit failure")

    monkeypatch.setattr(state.admin, "_append_audit", fail)
    with pytest.raises(RuntimeError):
        state.admin.create_account(username, "Must roll back", state.password)
    with state.pg.data.database.transaction() as conn:
        assert (
            conn.execute("SELECT user_id FROM public.users WHERE username=%s", (username,)).fetchone() is None
        )


def test_secure_http_login_logout_and_fail_closed_audit(state, tmp_path, monkeypatch):
    pg = state.pg
    app = create_app(
        Settings(environment="test", a_mode="persistent", auth_mode="password", storage_dir=tmp_path),
        data_binding=DataBinding(data=pg.data, ready=pg.data.database.ready),
        auth_sessions=state.store,
    )
    with TestClient(app, base_url="https://testserver", headers={"X-PaperPilot-Client": "web"}) as client:
        body = {"username": state.username, "password": state.password}
        assert client.post("/api/v1/auth/login", json={"username": state.username}).status_code == 401
        original = pg.data.append_audit

        def fail(event):
            if event.action == "auth.login":
                raise RuntimeError("PRIVATE_PASSWORD")
            return original(event)

        monkeypatch.setattr(pg.data, "append_audit", fail)
        denied = client.post("/api/v1/auth/login", json=body)
        assert (
            denied.status_code == 503
            and "set-cookie" not in denied.headers
            and state.password not in denied.text
        )
        monkeypatch.setattr(pg.data, "append_audit", original)
        result = client.post("/api/v1/auth/login", json=body)
        assert (
            result.status_code == 200
            and result.json()["authentication"] == "password"
            and not result.json()["mock"]
        )
        assert "Secure" in result.headers["set-cookie"] and "HttpOnly" in result.headers["set-cookie"]
        assert client.get("/api/v1/auth/me").status_code == 200
        assert client.post("/api/v1/auth/logout").status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 401
    with TestClient(app, base_url="http://testserver", headers={"X-PaperPilot-Client": "web"}) as insecure:
        assert insecure.post("/api/v1/auth/login", json=body).status_code == 403
