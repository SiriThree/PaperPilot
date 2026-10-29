"""Database-native project isolation candidate on disposable PostgreSQL only."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets
from threading import Barrier, Event
import asyncio
import time
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.rows import dict_row

from app.adapters.ports import Context, FileReference
from app.data.binding import compose_data_binding
from app.data.scoped_business import (
    PROJECT_TABLES,
    INSERT_COLUMNS,
    REPORT_INSERT_COLUMNS,
    REPORT_UPDATE_COLUMNS,
    WORKER_INSERT_COLUMNS,
    WORKER_UPDATE_COLUMNS,
    FAILURE_INSERT_COLUMNS,
    FAILURE_UPDATE_COLUMNS,
    RESULT_INSERT_COLUMNS,
    RESULT_UPDATE_COLUMNS,
    ScopedBusinessConfig,
    ScopedBusinessReader,
    ScopedBusinessRouter,
    ScopedReportWriter,
    ScopedBusinessWriter,
    ScopedClaimWorker,
    ScopedFailureWorker,
    ScopedResultWorker,
)
from app.data.settings import DataSettings
from app.errors import AppError
from app.config import Settings
from app.main import create_app
from app.schemas import AnswerResult, DraftResult, ProjectCreate, ReportSave
from data_contracts.support import (
    ready_paper,
    report_input,
    research_payload,
    processing_result,
    evidence_result,
)
from test_phase5a_persistent import database  # noqa: F401
from test_scoped_business_config import protected_file

pytestmark = pytest.mark.real_database


@pytest.fixture
def scoped_business(database):  # noqa: F811
    config, _ = database
    binding = compose_data_binding(config, DataSettings())
    data = binding.data
    alice, bob = data.user_by_name("alice"), data.user_by_name("bob")
    projects = [
        data.create_project(u.user_id, ProjectCreate(name=f"RLS {i}"), uuid4())
        for i, u in enumerate((alice, bob))
    ]
    contexts = [
        Context(u.user_id, p.project_id, uuid4()) for u, p in zip((alice, bob), projects, strict=True)
    ]
    rows = []
    for ctx in contexts:
        paper, task, result = ready_paper(data, ctx)
        report = data.save_report(ctx, report_input(result), str(uuid4()))
        rows.append((paper, task, report))
    data.add_member(contexts[1], alice.username)
    owner = "rls_owner_" + uuid4().hex[:10]
    identities = [("rls_reader_" + uuid4().hex[:10], secrets.token_urlsafe(32), ctx) for ctx in contexts]
    member_ctx = Context(alice.user_id, projects[1].project_id, uuid4())
    identities.append(("rls_member_" + uuid4().hex[:10], secrets.token_urlsafe(32), member_ctx))
    writer = ("rls_writer_" + uuid4().hex[:10], secrets.token_urlsafe(32), contexts[0])
    migration = (
        Path(__file__).resolve().parents[1] / "app/security/migrations/005_candidate_business_rls.sql"
    ).read_text(encoding="utf-8-sig")
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(migration, prepare=False)
        active_migration = (
            Path(__file__).resolve().parents[1]
            / "app/security/migrations/012_candidate_business_active_identity.sql"
        ).read_text(encoding="utf-8-sig")
        admin.execute(active_migration, prepare=False)
        admin.execute(
            sql.SQL("REVOKE CREATE,TEMPORARY ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(config.dbname)
            )
        )
        admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("GRANT USAGE,CREATE ON SCHEMA paperpilot_business TO {}").format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("GRANT SELECT ON paperpilot_business.project_logins TO {}").format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,disabled) ON public.users TO {}").format(sql.Identifier(owner))
        )
        for function in ("bound_project()", "bound_identity(uuid,uuid)"):
            admin.execute(
                sql.SQL("ALTER FUNCTION paperpilot_business.{} OWNER TO {}").format(
                    sql.SQL(function), sql.Identifier(owner)
                )
            )
        admin.execute(
            sql.SQL("REVOKE CREATE ON SCHEMA paperpilot_business FROM {}").format(sql.Identifier(owner))
        )
        for role, secret, ctx in [*identities, writer]:
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
                ).format(sql.Identifier(role), sql.Literal(secret))
            )
            admin.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public,paperpilot_business TO {}").format(sql.Identifier(role))
            )
            admin.execute(
                sql.SQL(
                    "GRANT EXECUTE ON FUNCTION paperpilot_business.bound_project(),paperpilot_business.bound_identity(uuid,uuid) TO {}"
                ).format(sql.Identifier(role))
            )
            admin.execute(
                "INSERT INTO paperpilot_business.project_logins VALUES (%s,%s,%s)",
                (role, ctx.user_id, ctx.project_id),
            )
        for role, _, _ in identities:
            admin.execute(
                sql.SQL("GRANT SELECT (user_id,username,display_name) ON public.users TO {}").format(
                    sql.Identifier(role)
                )
            )
            for table in sorted(PROJECT_TABLES):
                admin.execute(
                    sql.SQL("GRANT SELECT ON public.{} TO {}").format(
                        sql.Identifier(table), sql.Identifier(role)
                    )
                )
        admin.execute(
            sql.SQL("GRANT SELECT,INSERT,UPDATE ON public.papers TO {}").format(sql.Identifier(writer[0]))
        )
    readers = [
        ScopedBusinessReader(
            ScopedBusinessConfig(
                replace(config, user=role, password=secret), ctx.user_id, ctx.project_id, owner, config.user
            )
        )
        for role, secret, ctx in identities
    ]
    try:
        yield config, data, contexts, rows, readers, identities, writer
    finally:
        for reader in readers:
            reader.close()
        binding.close()


def test_project_readers_use_rls_and_server_bound_identity(scoped_business):
    config, _, contexts, rows, readers, _, _ = scoped_business
    for i in range(2):
        reader, ctx = readers[i], contexts[i]
        # Preserve actionable diagnostics in the isolated test only.
        with psycopg.connect(**reader.config.connection.conninfo(), row_factory=dict_row) as conn:
            reader._verify_database(conn)
        assert reader.ready()
        assert [p.paper_id for p in reader.papers(ctx)] == [rows[i][0].paper_id]
        assert reader.paper(ctx, rows[i][0].paper_id).paper_id == rows[i][0].paper_id
        assert reader.task(ctx, rows[i][1].task_id).task_id == rows[i][1].task_id
        assert reader.versions(ctx, rows[i][2].report_id)[0].version_id == rows[i][2].version_id
        assert reader.sources(ctx, rows[i][0].paper_id)
        with pytest.raises(AppError) as denied:
            reader.paper(ctx, rows[1 - i][0].paper_id)
        assert denied.value.status == 404
        with pytest.raises(AppError):
            reader.papers(Context(contexts[1 - i].user_id, ctx.project_id, uuid4()))
        with psycopg.connect(**reader.config.connection.conninfo()) as conn:
            conn.execute(
                "SELECT set_config('paperpilot.project_id',%s,true)", (str(contexts[1 - i].project_id),)
            )
            result = conn.execute("SELECT project_id FROM public.papers").fetchall()
            assert result == [(ctx.project_id,)]
            assert (
                conn.execute(
                    "SELECT count(*) FROM public.papers WHERE project_id=%s", (contexts[1 - i].project_id,)
                ).fetchone()[0]
                == 0
            )
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        with pytest.raises(psycopg.errors.UniqueViolation):
            admin.execute(
                "INSERT INTO paperpilot_business.project_logins VALUES(%s,%s,%s)",
                (readers[0].config.connection.user, contexts[1].user_id, contexts[1].project_id),
            )


def test_active_identity_baseline_and_owner_grants_fail_closed(scoped_business):
    config, _, _, _, readers, _, _ = scoped_business
    reader = readers[0]
    owner = reader.config.function_owner
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT SELECT (password_hash) ON public.users TO {}").format(sql.Identifier(owner))
        )
    with pytest.raises(AppError):
        reader.ready()
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("REVOKE SELECT (password_hash) ON public.users FROM {}").format(sql.Identifier(owner))
        )
        admin.execute(
            "CREATE OR REPLACE FUNCTION paperpilot_business.bound_project() RETURNS uuid "
            "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$ "
            "SELECT project_id FROM paperpilot_business.project_logins WHERE login_role = SESSION_USER $$"
        )
    with pytest.raises(AppError):
        reader.ready()
    migration = (
        Path(__file__).resolve().parents[1]
        / "app/security/migrations/012_candidate_business_active_identity.sql"
    ).read_text(encoding="utf-8-sig")
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(migration, prepare=False)
    assert reader.ready()


def test_rls_denies_cross_project_writes_and_revocation(scoped_business):
    config, data, contexts, rows, readers, _, writer = scoped_business
    writer_config = replace(config, user=writer[0], password=writer[1])
    with psycopg.connect(**writer_config.conninfo()) as conn:
        assert (
            conn.execute(
                "UPDATE public.papers SET title='forbidden' WHERE project_id=%s", (contexts[1].project_id,)
            ).rowcount
            == 0
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO public.papers(metadata_id,project_id,title,year) VALUES(%s,%s,'forbidden',2024)",
                (uuid4(), contexts[1].project_id),
            )
    member = readers[2]
    assert member.ready()
    data.remove_member(contexts[1], contexts[0].user_id)
    with pytest.raises(AppError) as revoked:
        member.ready()
    assert revoked.value.status == 503
    with psycopg.connect(**member.config.connection.conninfo()) as conn:
        assert conn.execute("SELECT count(*) FROM public.papers").fetchone()[0] == 0
    assert readers[0].paper(contexts[0], rows[0][0].paper_id).paper_id == rows[0][0].paper_id


def test_policy_and_role_drift_refuse_readiness(scoped_business):
    config, _, contexts, _, readers, _, _ = scoped_business
    role = readers[0].config.connection.user
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(sql.SQL("GRANT SELECT ON public.audit_events TO {}").format(sql.Identifier(role)))
    with pytest.raises(AppError):
        readers[0].ready()
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(sql.SQL("REVOKE SELECT ON public.audit_events FROM {}").format(sql.Identifier(role)))
        admin.execute("ALTER TABLE public.papers DISABLE ROW LEVEL SECURITY")
    with pytest.raises(AppError):
        readers[0].papers(contexts[0])


def test_missing_or_wrong_binding_and_column_grants_refuse_readiness(scoped_business):
    config, _, contexts, _, readers, _, _ = scoped_business
    reader = readers[0]
    wrong = ScopedBusinessReader(replace(reader.config, project_id=contexts[1].project_id))
    try:
        with pytest.raises(AppError) as mismatch:
            wrong.ready()
        assert mismatch.value.status == 503
    finally:
        wrong.close()
    role = reader.config.connection.user
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(sql.SQL("GRANT SELECT ON public.users TO {}").format(sql.Identifier(role)))
    with pytest.raises(AppError):
        reader.ready()
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(sql.SQL("REVOKE SELECT ON public.users FROM {}").format(sql.Identifier(role)))
        admin.execute("DELETE FROM paperpilot_business.project_logins WHERE login_role=%s", (role,))
    with pytest.raises(AppError):
        reader.ready()
    with psycopg.connect(**reader.config.connection.conninfo()) as conn:
        assert conn.execute("SELECT count(*) FROM public.papers").fetchone()[0] == 0


def test_api_routes_use_bound_reader_and_never_fall_back(scoped_business, monkeypatch):
    config, data, contexts, rows, readers, _, _ = scoped_business
    ctx = contexts[0]
    router = ScopedBusinessRouter([readers[0]])
    app = create_app(Settings(environment="test"), scoped_business_reader=router)
    mock_data = app.state.adapters.data
    user = data.user_by_id(ctx.user_id)
    monkeypatch.setattr(mock_data, "user_by_id", lambda user_id: user if user_id == ctx.user_id else None)
    monkeypatch.setattr(
        mock_data, "role", lambda user_id, project_id: "owner" if user_id == ctx.user_id else None
    )
    monkeypatch.setattr(mock_data, "papers", lambda *_args: pytest.fail("unscoped fallback"))
    with TestClient(app) as client:
        client.cookies.set("pp_session", app.state.sessions.create(ctx.user_id), path="/api/v1")
        root = f"/api/v1/projects/{ctx.project_id}"
        assert client.get(root + "/papers").json()["items"][0]["paper_id"] == str(rows[0][0].paper_id)
        assert client.get(root + f"/tasks/{rows[0][1].task_id}").status_code == 200
        assert client.get(root + f"/reports/{rows[0][2].report_id}/versions").status_code == 200
        assert client.post(root + "/papers", headers={"X-PaperPilot-Client": "web"}).status_code == 503
        assert (
            client.options(
                root + "/papers",
                headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
            ).status_code
            == 200
        )
        assert app.state.worker_task is None
        # The authorization stub permits the second project, but no matching DB identity exists.
        assert client.get(f"/api/v1/projects/{contexts[1].project_id}/papers").status_code == 503
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "DELETE FROM paperpilot_business.project_logins WHERE login_role=%s",
                (readers[0].config.connection.user,),
            )
        assert client.get(root + "/papers").status_code == 503
        assert client.get("/api/v1/health").status_code == 503


def test_composition_uses_protected_multi_project_business_manifest(scoped_business, monkeypatch, tmp_path):  # noqa: F811
    from app.data.database import ConnectionConfig
    from app.dev_composition import create_app as create_composed

    config, _, contexts, rows, _, identities, _ = scoped_business
    owner = scoped_business[4][0].config.function_owner
    entries = [
        {
            "db_user": role,
            "db_password": secret,
            "user_id": str(ctx.user_id),
            "project_id": str(ctx.project_id),
            "profile": "read",
        }
        for role, secret, ctx in identities[:2]
    ]
    secret_file = protected_file(tmp_path / "scoped.json", json.dumps(entries))
    for key, value in {
        "PAPERPILOT_SCOPED_BUSINESS_ISOLATED_TEST": "1",
        "PAPERPILOT_SCOPED_BUSINESS_BINDINGS_FILE": str(secret_file),
        "PAPERPILOT_SCOPED_BUSINESS_FUNCTION_OWNER": owner,
        "PAPERPILOT_SCOPED_BUSINESS_BINDING_OWNER": config.user,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(ConnectionConfig, "from_env", lambda: config)
    app = create_composed(Settings(environment="test", a_mode="persistent"))
    with TestClient(app) as client:
        client.cookies.set("pp_session", app.state.sessions.create(contexts[0].user_id), path="/api/v1")
        own = client.get(f"/api/v1/projects/{contexts[0].project_id}/papers")
        assert own.status_code == 200
        assert own.json()["items"][0]["paper_id"] == str(rows[0][0].paper_id)
        cross = client.get(f"/api/v1/projects/{contexts[1].project_id}/papers/{rows[1][0].paper_id}")
        assert cross.status_code == 503  # Alice is a member, but no Alice/project-B database mapping exists.
        client.cookies.set("pp_session", app.state.sessions.create(contexts[1].user_id), path="/api/v1")
        assert client.get(f"/api/v1/projects/{contexts[1].project_id}/papers").status_code == 200
        assert client.get(f"/api/v1/projects/{contexts[0].project_id}/papers").status_code == 404
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("UPDATE public.users SET disabled=true WHERE user_id=%s", (contexts[1].user_id,))
        assert client.get("/api/v1/health").status_code == 503
        assert client.get(f"/api/v1/projects/{contexts[1].project_id}/papers").status_code == 503
    old_secret = entries[0]["db_password"]
    rotated_secret = secrets.token_urlsafe(32)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("UPDATE public.users SET disabled=false WHERE user_id=%s", (contexts[1].user_id,))
        admin.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(identities[0][0]), sql.Literal(rotated_secret)
            )
        )
    entries[0]["db_password"] = rotated_secret
    secret_file.write_text(json.dumps(entries), encoding="utf-8")
    app = create_composed(Settings(environment="test", a_mode="persistent"))
    with TestClient(app) as client:
        client.cookies.set("pp_session", app.state.sessions.create(contexts[0].user_id), path="/api/v1")
        assert client.get(f"/api/v1/projects/{contexts[0].project_id}/papers").status_code == 200
    entries[0]["db_password"] = old_secret
    secret_file.write_text(json.dumps(entries), encoding="utf-8")
    app = create_composed(Settings(environment="test", a_mode="persistent"))
    with pytest.raises(AppError):
        with TestClient(app):
            pytest.fail("old database credential must not start the pilot")


def test_business_router_rejects_duplicate_identities_and_production(scoped_business):
    _, _, _, _, readers, _, _ = scoped_business
    with pytest.raises(ValueError):
        ScopedBusinessRouter([readers[0], readers[0]])
    with pytest.raises(RuntimeError, match="isolated test"):
        create_app(Settings(environment="production"), scoped_business_reader=readers[0])


def test_upload_api_uses_bound_writer_and_fails_closed(scoped_business, monkeypatch, tmp_path):  # noqa: F811
    config, data, contexts, _, readers, _, (role, password, ctx) = scoped_business
    migration = (
        Path(__file__).resolve().parents[1]
        / "app/security/migrations/006_candidate_business_actor_checks.sql"
    ).read_text(encoding="utf-8-sig")
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(migration, prepare=False)
        admin.execute(sql.SQL("REVOKE INSERT,UPDATE ON public.papers FROM {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,username,display_name) ON public.users TO {}").format(
                sql.Identifier(role)
            )
        )
        for table in PROJECT_TABLES:
            admin.execute(
                sql.SQL("GRANT SELECT ON public.{} TO {}").format(sql.Identifier(table), sql.Identifier(role))
            )
        for table, columns in INSERT_COLUMNS.items():
            admin.execute(
                sql.SQL("GRANT INSERT ({}) ON public.{} TO {}").format(
                    sql.SQL(",").join(map(sql.Identifier, sorted(columns))),
                    sql.Identifier(table),
                    sql.Identifier(role),
                )
            )
    writer_config = ScopedBusinessConfig(
        replace(config, user=role, password=password),
        ctx.user_id,
        ctx.project_id,
        readers[0].config.function_owner,
        config.user,
    )
    audit_events = []

    def make_app():
        writer = ScopedBusinessWriter(writer_config)
        app = create_app(
            Settings(environment="test", storage_dir=tmp_path),
            scoped_business_reader=ScopedBusinessRouter([writer]),
        )
        # Authentication and audit are development mocks; data writes use the real restricted login.
        mock_data = app.state.adapters.data
        monkeypatch.setattr(mock_data, "user_by_id", data.user_by_id)
        monkeypatch.setattr(mock_data, "role", data.role)
        monkeypatch.setattr(mock_data, "append_audit", audit_events.append)
        monkeypatch.setattr(mock_data, "import_paper", lambda *_args: pytest.fail("unscoped upload fallback"))
        return app

    root = f"/api/v1/projects/{ctx.project_id}"
    headers = {"X-PaperPilot-Client": "web", "Idempotency-Key": "scoped-api-upload"}
    files = {"file": ("fixture.pdf", b"%PDF-1.4\nfixture", "application/pdf")}
    app = make_app()
    with TestClient(app) as client:
        assert "scoped_business_upload_pilot" in client.get("/api/v1/health").json()["limitations"]
        assert (
            client.post(
                root + "/papers", data={"title": "Scoped API"}, files=files, headers=headers
            ).status_code
            == 401
        )
        client.cookies.set("pp_session", app.state.sessions.create(ctx.user_id), path="/api/v1")

        def audit_failure(_event):
            raise RuntimeError("controlled audit failure")

        monkeypatch.setattr(app.state.adapters.data, "append_audit", audit_failure)
        failed_audit = client.post(
            root + "/papers", data={"title": "Scoped API"}, files=files, headers=headers
        )
        assert failed_audit.status_code == 503
        assert failed_audit.json()["code"] == "AUDIT_UNAVAILABLE"
        assert list(tmp_path.iterdir()) == []
        monkeypatch.setattr(app.state.adapters.data, "append_audit", audit_events.append)
        first = client.post(root + "/papers", data={"title": "Scoped API"}, files=files, headers=headers)
        assert first.status_code == 202, first.text
        repeated = client.post(root + "/papers", data={"title": "Scoped API"}, files=files, headers=headers)
        assert repeated.status_code == 202, repeated.text
        assert repeated.json() == first.json()
        assert len(audit_events) == 2
        task_id = first.json()["task"]["task_id"]
        paper_id = first.json()["paper"]["paper_id"]
        assert len(list(tmp_path.iterdir())) == 1
        assert (
            client.post(
                f"/api/v1/projects/{contexts[1].project_id}/papers",
                data={"title": "Other"},
                files=files,
                headers=headers,
            ).status_code
            == 503
        )
        assert len(list(tmp_path.iterdir())) == 1
        assert app.state.worker_task is None
    app = make_app()
    with TestClient(app) as client:
        client.cookies.set("pp_session", app.state.sessions.create(ctx.user_id), path="/api/v1")
        assert client.get(root + f"/tasks/{task_id}").json()["task_id"] == task_id
        assert client.get(root + f"/papers/{paper_id}").json()["paper"]["paper_id"] == paper_id
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("DELETE FROM paperpilot_business.project_logins WHERE login_role=%s", (role,))
        denied = client.post(
            root + "/papers",
            data={"title": "Revoked"},
            files=files,
            headers={**headers, "Idempotency-Key": "revoked"},
        )
        assert denied.status_code == 503
        assert len(list(tmp_path.iterdir())) == 1
        assert client.get("/api/v1/health").status_code == 503


def test_isolated_worker_lifecycle_processes_upload_with_bound_identity(
    scoped_business, monkeypatch, tmp_path, request
):  # noqa: F811
    from app.data.adapter import PostgresData
    from app.data.database import PostgresDatabase
    from app.data.deliverer import OutboxDeliverer
    from app.data.scoped_worker_audit import ScopedWorkerAuditWriter

    config, data, contexts, _, readers, _, (writer_role, writer_secret, ctx) = scoped_business
    worker_role, worker_secret = "worker_pilot_" + uuid4().hex[:10], secrets.token_urlsafe(32)
    audit_role, audit_secret = "worker_audit_" + uuid4().hex[:10], secrets.token_urlsafe(32)
    audit_owner = "worker_audit_owner_" + uuid4().hex[:10]
    delivery_role, delivery_secret = "worker_outbox_" + uuid4().hex[:10], secrets.token_urlsafe(32)
    migrations = Path(__file__).resolve().parents[1] / "app/security/migrations"
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        for name in (
            "006_candidate_business_actor_checks.sql",
            "008_candidate_worker_actor_checks.sql",
            "009_candidate_worker_result_checks.sql",
        ):
            admin.execute((migrations / name).read_text(encoding="utf-8-sig"), prepare=False)
        admin.execute(
            sql.SQL("REVOKE INSERT,UPDATE ON public.papers FROM {}").format(sql.Identifier(writer_role))
        )
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(worker_role), sql.Literal(worker_secret))
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public,paperpilot_business TO {}").format(
                sql.Identifier(worker_role)
            )
        )
        admin.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION paperpilot_business.bound_project(),paperpilot_business.bound_identity(uuid,uuid),paperpilot_business.worker_has_lease(uuid,uuid,integer),paperpilot_business.worker_has_result(uuid,uuid),paperpilot_business.worker_has_process(uuid,uuid) TO {}"
            ).format(sql.Identifier(worker_role))
        )
        admin.execute(
            "INSERT INTO paperpilot_business.project_logins VALUES (%s,%s,%s)",
            (worker_role, ctx.user_id, ctx.project_id),
        )
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(audit_owner))
        )
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(audit_role), sql.Literal(audit_secret))
        )
        admin.execute(
            sql.SQL("GRANT SELECT ON public.audit_events TO {}").format(sql.Identifier(audit_owner))
        )
        admin.execute(
            sql.SQL("GRANT USAGE,CREATE ON SCHEMA paperpilot_audit TO {}").format(sql.Identifier(audit_owner))
        )
        admin.execute(
            sql.SQL("ALTER FUNCTION paperpilot_audit.event_fingerprint(uuid) OWNER TO {}").format(
                sql.Identifier(audit_owner)
            )
        )
        admin.execute(
            sql.SQL("REVOKE CREATE ON SCHEMA paperpilot_audit FROM {}").format(sql.Identifier(audit_owner))
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public,paperpilot_business,paperpilot_audit TO {}").format(
                sql.Identifier(audit_role)
            )
        )
        admin.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION paperpilot_business.bound_project(),paperpilot_audit.event_fingerprint(uuid) TO {}"
            ).format(sql.Identifier(audit_role))
        )
        admin.execute(
            sql.SQL("GRANT SELECT (project_id) ON public.projects TO {}").format(sql.Identifier(audit_role))
        )
        admin.execute(
            sql.SQL("GRANT SELECT (user_id) ON public.users TO {}").format(sql.Identifier(audit_role))
        )
        admin.execute(sql.SQL("GRANT INSERT ON public.audit_events TO {}").format(sql.Identifier(audit_role)))
        admin.execute(
            "INSERT INTO paperpilot_business.project_logins VALUES (%s,%s,%s)",
            (audit_role, ctx.user_id, ctx.project_id),
        )
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(delivery_role), sql.Literal(delivery_secret))
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public,paperpilot_business TO {}").format(
                sql.Identifier(delivery_role)
            )
        )
        admin.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION paperpilot_business.bound_project(),paperpilot_business.bound_identity(uuid,uuid) TO {}"
            ).format(sql.Identifier(delivery_role))
        )
        admin.execute(
            sql.SQL("GRANT SELECT ON public.outbox_events TO {}").format(sql.Identifier(delivery_role))
        )
        admin.execute(
            sql.SQL(
                "GRANT UPDATE (dead_lettered_at,last_error_code,delivery_attempts,next_attempt_at,last_attempt_at,published_at) ON public.outbox_events TO {}"
            ).format(sql.Identifier(delivery_role))
        )
        admin.execute(
            "INSERT INTO paperpilot_business.project_logins VALUES (%s,%s,%s)",
            (delivery_role, ctx.user_id, ctx.project_id),
        )
        for role in (writer_role, worker_role):
            admin.execute(
                sql.SQL("GRANT SELECT (user_id,username,display_name) ON public.users TO {}").format(
                    sql.Identifier(role)
                )
            )
            for table in PROJECT_TABLES:
                admin.execute(
                    sql.SQL("GRANT SELECT ON public.{} TO {}").format(
                        sql.Identifier(table), sql.Identifier(role)
                    )
                )
        for role, permission, profile in (
            (writer_role, "INSERT", INSERT_COLUMNS),
            (worker_role, "INSERT", RESULT_INSERT_COLUMNS),
            (worker_role, "UPDATE", RESULT_UPDATE_COLUMNS),
        ):
            for table, columns in profile.items():
                admin.execute(
                    sql.SQL("GRANT {} ({}) ON public.{} TO {}").format(
                        sql.SQL(permission),
                        sql.SQL(",").join(map(sql.Identifier, sorted(columns))),
                        sql.Identifier(table),
                        sql.Identifier(role),
                    )
                )
    owner = readers[0].config.function_owner
    writer = ScopedBusinessWriter(
        ScopedBusinessConfig(
            replace(config, user=writer_role, password=writer_secret),
            ctx.user_id,
            ctx.project_id,
            owner,
            config.user,
        )
    )
    worker = ScopedResultWorker(
        ScopedBusinessConfig(
            replace(config, user=worker_role, password=worker_secret),
            ctx.user_id,
            ctx.project_id,
            owner,
            config.user,
        )
    )
    audit_database = PostgresDatabase(replace(config, user=audit_role, password=audit_secret), pool_size=1)
    request.addfinalizer(audit_database.close)
    audit_data = PostgresData(audit_database, DataSettings())
    delivery_database = PostgresDatabase(
        replace(config, user=delivery_role, password=delivery_secret), pool_size=1
    )
    request.addfinalizer(delivery_database.close)
    events = []
    audit_failed = Event()

    def audit_sink(event):
        try:
            audit_data.append_audit(event)
        except Exception:
            audit_failed.set()
            raise
        events.append(event)

    startup_reader = ScopedBusinessWriter(writer.config)
    startup_worker = ScopedResultWorker(worker.config)
    closed = []
    original_close = startup_worker.close

    def close_startup_worker():
        closed.append(True)
        original_close()
        raise RuntimeError("controlled close failure")

    monkeypatch.setattr(startup_worker, "ready", lambda: False)
    monkeypatch.setattr(startup_worker, "close", close_startup_worker)
    failed_app = create_app(
        Settings(environment="test"),
        scoped_business_reader=ScopedBusinessRouter([startup_reader]),
        scoped_workers=[startup_worker],
        scoped_worker_audit=audit_sink,
    )
    with pytest.raises(RuntimeError, match="Scoped worker readiness failed"):
        with TestClient(failed_app):
            pytest.fail("unready worker must not start")
    assert closed == [True]
    audit_startup_reader = ScopedBusinessWriter(writer.config)
    audit_startup_worker = ScopedResultWorker(worker.config)
    audit_failed_app = create_app(
        Settings(environment="test"),
        scoped_business_reader=ScopedBusinessRouter([audit_startup_reader]),
        scoped_workers=[audit_startup_worker],
        scoped_worker_audit=audit_sink,
        scoped_worker_audit_ready=lambda: False,
    )
    with pytest.raises(RuntimeError, match="Scoped worker audit readiness failed"):
        with TestClient(audit_failed_app):
            pytest.fail("unready audit must not start")
    router = ScopedBusinessRouter([writer])
    with pytest.raises(RuntimeError, match="explicit audit sink"):
        create_app(Settings(environment="test"), scoped_business_reader=router, scoped_workers=[worker])
    with pytest.raises(RuntimeError, match="Duplicate"):
        create_app(
            Settings(environment="test"),
            scoped_business_reader=router,
            scoped_workers=[worker, worker],
            scoped_worker_audit=audit_sink,
        )
    app = create_app(
        Settings(environment="test", storage_dir=tmp_path),
        scoped_business_reader=router,
        scoped_workers=[worker],
        scoped_worker_audit=audit_sink,
        scoped_worker_audit_ready=lambda: getattr(audit_data, "ready", lambda: True)(),
    )
    mock_data = app.state.adapters.data
    monkeypatch.setattr(mock_data, "user_by_id", data.user_by_id)
    monkeypatch.setattr(mock_data, "role", data.role)
    monkeypatch.setattr(mock_data, "append_audit", audit_sink)
    monkeypatch.setattr(mock_data, "import_paper", lambda *_args: pytest.fail("unscoped import"))
    monkeypatch.setattr(mock_data, "claim_job", lambda: pytest.fail("unscoped worker claim"))
    monkeypatch.setattr(mock_data, "complete_job", lambda *_args: pytest.fail("unscoped worker completion"))
    with TestClient(app) as client:
        assert "scoped_worker_mock_pilot" in client.get("/api/v1/health").json()["limitations"]
        client.cookies.set("pp_session", app.state.sessions.create(ctx.user_id), path="/api/v1")
        root = f"/api/v1/projects/{ctx.project_id}"
        response = client.post(
            root + "/papers",
            data={"title": "Worker pilot"},
            files={"file": ("fixture.pdf", b"%PDF-1.4\nfixture", "application/pdf")},
            headers={"X-PaperPilot-Client": "web", "Idempotency-Key": "worker-pilot"},
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["task"]["task_id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            task = client.get(root + f"/tasks/{task_id}")
            assert task.status_code == 200
            if task.json()["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.1)
        assert task.json()["status"] == "succeeded"
        detail = client.get(root + f"/papers/{response.json()['paper']['paper_id']}").json()
        assert detail["paper"]["status"] == "searchable"
        assert detail["sources"] and detail["sources"][0]["synthetic"] is True
        assert any(event.action == "process.completed" for event in events)
        with psycopg.connect(**config.conninfo()) as admin:
            persisted = admin.execute(
                "SELECT count(*) FROM public.audit_events WHERE request_id=%s AND project_id=%s "
                "AND actor_id=%s AND action='process.completed'",
                (task.json()["request_id"], ctx.project_id, ctx.user_id),
            ).fetchone()[0]
            assert persisted == 1
        with psycopg.connect(**audit_database.config.conninfo()) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM public.projects WHERE project_id=%s", (contexts[1].project_id,)
                ).fetchone()[0]
                == 0
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT * FROM public.audit_events")
        # Demonstrate the remaining risk without retaining a forged event: the
        # candidate audit login has table INSERT but no native actor/project check.
        forged_event = uuid4()
        with psycopg.connect(**audit_database.config.conninfo()) as conn:
            cursor = conn.execute(
                "INSERT INTO public.audit_events(event_id,timestamp,request_id,actor_id,project_id,"
                "action,resource_type,decision,execution_status,duration_ms,mock,event_fingerprint) "
                "VALUES(%s,now(),%s,%s,%s,'fixture.forged','fixture','allowed','completed',0,true,%s)",
                (forged_event, uuid4(), contexts[1].user_id, contexts[1].project_id, "f" * 64),
            )
            assert cursor.rowcount == 1
            conn.rollback()
        with psycopg.connect(**config.conninfo()) as admin:
            assert (
                admin.execute(
                    "SELECT count(*) FROM public.audit_events WHERE event_id=%s", (forged_event,)
                ).fetchone()[0]
                == 0
            )

        fingerprint_owner = "paperpilot_audit_worker_fingerprint_owner"
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (fingerprint_owner,)).fetchone():
                admin.execute(
                    sql.SQL(
                        "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
                    ).format(sql.Identifier(fingerprint_owner))
                )
            admin.execute(
                sql.SQL("GRANT SELECT ON public.audit_events TO {}").format(sql.Identifier(fingerprint_owner))
            )
            admin.execute(
                sql.SQL("GRANT USAGE,CREATE ON SCHEMA paperpilot_audit TO {}").format(
                    sql.Identifier(fingerprint_owner)
                )
            )
            admin.execute(
                sql.SQL("ALTER FUNCTION paperpilot_audit.event_fingerprint(uuid) OWNER TO {}").format(
                    sql.Identifier(fingerprint_owner)
                )
            )
            admin.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA paperpilot_audit FROM {}").format(
                    sql.Identifier(fingerprint_owner)
                )
            )
            admin.execute(
                sql.SQL("REVOKE SELECT ON public.audit_events FROM {}").format(sql.Identifier(audit_owner))
            )
            admin.execute(
                sql.SQL(
                    "GRANT EXECUTE ON FUNCTION paperpilot_business.bound_identity(uuid,uuid) TO {}"
                ).format(sql.Identifier(audit_role))
            )
            admin.execute(
                (migrations / "013_candidate_worker_audit_rls.sql").read_text(encoding="utf-8-sig"),
                prepare=False,
            )
        guarded_audit = ScopedWorkerAuditWriter(
            ScopedBusinessConfig(
                replace(config, user=audit_role, password=audit_secret),
                ctx.user_id,
                ctx.project_id,
                owner,
                config.user,
            )
        )
        request.addfinalizer(guarded_audit.close)
        with psycopg.connect(**guarded_audit.database.config.conninfo(), row_factory=dict_row) as conn:
            guarded_audit._verify_database(conn)
        assert guarded_audit.ready()
        audit_data = guarded_audit
        allowed_event = events[-1].model_copy(update={"event_id": uuid4(), "action": "fixture.allowed"})
        audit_data.append_audit(allowed_event)
        for actor, project in (
            (contexts[1].user_id, contexts[1].project_id),
            (contexts[1].user_id, ctx.project_id),
            (ctx.user_id, None),
        ):
            with psycopg.connect(**audit_database.config.conninfo()) as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(
                        "INSERT INTO public.audit_events(event_id,timestamp,request_id,actor_id,project_id,"
                        "action,resource_type,decision,execution_status,duration_ms,mock,event_fingerprint) "
                        "VALUES(%s,now(),%s,%s,%s,'fixture.forged','fixture','allowed','completed',0,true,%s)",
                        (uuid4(), uuid4(), actor, project, "f" * 64),
                    )
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("ALTER POLICY worker_audit_insert ON public.audit_events WITH CHECK (true)")
        with pytest.raises(AppError):
            guarded_audit.ready()
        with pytest.raises(AppError):
            guarded_audit.append_audit(allowed_event.model_copy(update={"event_id": uuid4()}))
        assert client.get("/api/v1/health").status_code == 503
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "ALTER POLICY worker_audit_insert ON public.audit_events WITH CHECK "
                "(project_id=paperpilot_business.bound_project() AND paperpilot_business.bound_identity(actor_id,project_id))"
            )
            admin.execute(
                sql.SQL("GRANT SELECT (actor_id) ON public.audit_events TO {}").format(
                    sql.Identifier(audit_role)
                )
            )
        with pytest.raises(AppError):
            guarded_audit.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("REVOKE SELECT (actor_id) ON public.audit_events FROM {}").format(
                    sql.Identifier(audit_role)
                )
            )
            admin.execute("ALTER FUNCTION paperpilot_audit.event_fingerprint(uuid) SECURITY INVOKER")
        with pytest.raises(AppError):
            guarded_audit.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("ALTER FUNCTION paperpilot_audit.event_fingerprint(uuid) SECURITY DEFINER")
        assert guarded_audit.ready()
        assert client.get("/api/v1/health").status_code == 200
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(sql.SQL("ALTER ROLE {} INHERIT").format(sql.Identifier(audit_role)))
        with pytest.raises(AppError):
            guarded_audit.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(sql.SQL("ALTER ROLE {} NOINHERIT").format(sql.Identifier(audit_role)))
            admin.execute("UPDATE public.users SET disabled=true WHERE user_id=%s", (ctx.user_id,))
        with pytest.raises(AppError):
            guarded_audit.ready()
        with pytest.raises(AppError):
            guarded_audit.append_audit(allowed_event.model_copy(update={"event_id": uuid4()}))
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("UPDATE public.users SET disabled=false WHERE user_id=%s", (ctx.user_id,))
        assert guarded_audit.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            if not admin.execute(
                "SELECT 1 FROM pg_roles WHERE rolname='paperpilot_auth_audit_writer'"
            ).fetchone():
                admin.execute("CREATE ROLE paperpilot_auth_audit_writer LOGIN NOINHERIT")
            admin.execute(
                (migrations / "014_candidate_auth_audit_rls.sql").read_text(encoding="utf-8-sig"),
                prepare=False,
            )
        assert guarded_audit.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("ALTER POLICY auth_audit_completed_insert ON public.audit_events WITH CHECK (true)")
        with pytest.raises(AppError):
            guarded_audit.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "ALTER POLICY auth_audit_completed_insert ON public.audit_events WITH CHECK (project_id IS NULL AND resource_type='session' AND action IN ('auth.login','auth.logout','auth.session.invalid','auth.login.rate_limited') AND execution_status='completed' AND mock=false)"
            )
        assert guarded_audit.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("ALTER POLICY auth_audit_scope_cap ON public.audit_events WITH CHECK (true)")
        with pytest.raises(AppError):
            guarded_audit.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "ALTER POLICY auth_audit_scope_cap ON public.audit_events WITH CHECK (project_id IS NULL AND resource_type='session' AND action IN ('auth.login','auth.logout','auth.session.invalid','auth.login.rate_limited') AND execution_status='completed' AND mock=false)"
            )
        assert guarded_audit.ready()

        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            if not admin.execute(
                "SELECT 1 FROM pg_roles WHERE rolname='paperpilot_system_audit_writer'"
            ).fetchone():
                admin.execute("CREATE ROLE paperpilot_system_audit_writer LOGIN NOINHERIT")
            admin.execute(
                (migrations / "015_candidate_system_audit_rls.sql").read_text(encoding="utf-8-sig"),
                prepare=False,
            )
        assert guarded_audit.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("ALTER POLICY system_audit_scope_cap ON public.audit_events WITH CHECK (true)")
        with pytest.raises(AppError):
            guarded_audit.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "ALTER POLICY system_audit_scope_cap ON public.audit_events WITH CHECK "
                "(paperpilot_audit.system_event_allowed(project_id, action, resource_type, decision, execution_status, mock))"
            )
        assert guarded_audit.ready()

        started, release = Event(), Event()
        original_process = app.state.scoped_worker_services[0].adapters.ai.process

        async def pause_process(*args):
            started.set()
            if not await asyncio.to_thread(release.wait, 15):
                raise RuntimeError("fixture gate timed out")
            return await original_process(*args)

        monkeypatch.setattr(app.state.scoped_worker_services[0].adapters.ai, "process", pause_process)
        pending = client.post(
            root + "/papers",
            data={"title": "Audit fault"},
            files={"file": ("audit.pdf", b"%PDF-1.4\naudit fixture", "application/pdf")},
            headers={"X-PaperPilot-Client": "web", "Idempotency-Key": "worker-audit-fault"},
        )
        assert pending.status_code == 202
        pending_id = pending.json()["task"]["task_id"]
        pending_request = pending.json()["task"]["request_id"]
        try:
            assert started.wait(15)
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute(
                    sql.SQL("REVOKE INSERT ON public.audit_events FROM {}").format(sql.Identifier(audit_role))
                )
            release.set()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                fault_task = client.get(root + f"/tasks/{pending_id}")
                assert fault_task.status_code == 200
                if fault_task.json()["status"] == "succeeded":
                    break
                time.sleep(0.1)
            assert fault_task.json()["status"] == "succeeded"
            assert audit_failed.wait(15)
            with psycopg.connect(**config.conninfo()) as admin:
                assert (
                    admin.execute(
                        "SELECT count(*) FROM public.outbox_events WHERE request_id=%s AND payload->>'action'='complete_job'",
                        (pending_request,),
                    ).fetchone()[0]
                    == 1
                )
                assert (
                    admin.execute(
                        "SELECT count(*) FROM public.audit_events WHERE request_id=%s AND action='process.completed'",
                        (pending_request,),
                    ).fetchone()[0]
                    == 0
                )
        finally:
            release.set()
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute(
                    sql.SQL("GRANT INSERT ON public.audit_events TO {}").format(sql.Identifier(audit_role))
                )
        with delivery_database.transaction() as conn:
            outbox_row = conn.execute(
                "SELECT event_id,project_id,actor_id,request_id,payload,created_at,delivery_attempts "
                "FROM public.outbox_events WHERE request_id=%s AND payload->>'action'='complete_job'",
                (pending_request,),
            ).fetchone()
            assert outbox_row is not None
            assert (
                conn.execute(
                    "SELECT count(*) AS total FROM public.outbox_events WHERE project_id=%s",
                    (contexts[1].project_id,),
                ).fetchone()["total"]
                == 0
            )
        deliverer = OutboxDeliverer(delivery_database, audit_data)
        assert deliverer.deliver_one(outbox_row)
        assert not deliverer.deliver_one(outbox_row)
        with psycopg.connect(**config.conninfo()) as admin:
            assert (
                admin.execute(
                    "SELECT count(*) FROM public.audit_events WHERE event_id=%s AND action='complete_job'",
                    (outbox_row["event_id"],),
                ).fetchone()[0]
                == 1
            )
            assert admin.execute(
                "SELECT published_at IS NOT NULL FROM public.outbox_events WHERE event_id=%s",
                (outbox_row["event_id"],),
            ).fetchone()[0]

        async def fail_process(*_args):
            raise RuntimeError("controlled mock processing failure")

        monkeypatch.setattr(app.state.scoped_worker_services[0].adapters.ai, "process", fail_process)
        failed = client.post(
            root + "/papers",
            data={"title": "Failing worker"},
            files={"file": ("failed.pdf", b"%PDF-1.4\nanother fixture", "application/pdf")},
            headers={"X-PaperPilot-Client": "web", "Idempotency-Key": "worker-failure"},
        )
        assert failed.status_code == 202
        failed_id = failed.json()["task"]["task_id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            failed_task = client.get(root + f"/tasks/{failed_id}")
            assert failed_task.status_code == 200
            if failed_task.json()["status"] == "failed":
                break
            time.sleep(0.1)
        assert failed_task.json()["status"] == "failed"
        assert client.get("/api/v1/health").status_code == 200
        assert client.get(f"/api/v1/projects/{contexts[1].project_id}/papers").status_code == 503
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("UPDATE public.users SET disabled=true WHERE user_id=%s", (ctx.user_id,))
        assert client.get("/api/v1/health").status_code == 503


def test_auth_audit_candidate_keeps_session_and_audit_atomic(scoped_business):
    from app.data.authentication import PostgresSessions
    from app.data.authentication_boundary import AuthenticationBoundary
    from app.data.candidate_auth_audit import CandidateAuthAuditGuard
    from app.data.database import PostgresDatabase
    from app.data.scoped_system_audit import ScopedSystemAuditWriter
    from test_authentication_boundary_postgres import install_auth_lock

    config, data, contexts, _, readers, _, _ = scoped_business
    user = data.user_by_name("alice")
    login_password, role_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    admin_database = PostgresDatabase(config)
    try:
        PostgresSessions(admin_database).initialize_password("alice", login_password)
    finally:
        admin_database.close()
    role = "paperpilot_auth_audit_writer"
    fingerprint_owner = "paperpilot_audit_worker_fingerprint_owner"
    migrations = Path(__file__).resolve().parents[1] / "app/security/migrations"
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        for name, can_login in ((role, True), (fingerprint_owner, False)):
            if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone():
                admin.execute(
                    sql.SQL(
                        "CREATE ROLE {} {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
                    ).format(sql.Identifier(name), sql.SQL("LOGIN" if can_login else "NOLOGIN"))
                )
        admin.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(role_password))
        )
        admin.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,username,password_hash,disabled) ON public.users TO {}").format(
                sql.Identifier(role)
            )
        )
        admin.execute(
            sql.SQL(
                "GRANT SELECT,INSERT,UPDATE ON public.auth_sessions,public.auth_login_limits TO {}"
            ).format(sql.Identifier(role))
        )
        admin.execute(sql.SQL("GRANT INSERT ON public.audit_events TO {}").format(sql.Identifier(role)))
        admin.execute(sql.SQL("GRANT USAGE ON SCHEMA paperpilot_business TO {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION paperpilot_business.bound_project(),paperpilot_business.bound_identity(uuid,uuid) TO {}"
            ).format(sql.Identifier(role))
        )
        install_auth_lock(admin, role)
        admin.execute(
            (migrations / "013_candidate_worker_audit_rls.sql").read_text(encoding="utf-8-sig"), prepare=False
        )
        admin.execute(
            (migrations / "014_candidate_auth_audit_rls.sql").read_text(encoding="utf-8-sig"), prepare=False
        )

    auth_database = PostgresDatabase(replace(config, user=role, password=role_password))

    class IsolatedAuthDatabase:
        transaction = auth_database.transaction
        lock_credential = staticmethod(AuthenticationBoundary.lock_credential)

    sessions = PostgresSessions(IsolatedAuthDatabase())
    request_id = uuid4()
    try:
        with psycopg.connect(**auth_database.config.conninfo()) as conn:
            assert conn.execute("SELECT paperpilot_business.bound_project() IS NULL").fetchone()[0]
        token = sessions.create(user.user_id, login_password, request_id)
        assert sessions.current(token).user_id == user.user_id
        with psycopg.connect(**config.conninfo()) as admin:
            assert (
                admin.execute(
                    "SELECT count(*) FROM public.audit_events WHERE request_id=%s AND action='auth.login' AND project_id IS NULL",
                    (request_id,),
                ).fetchone()[0]
                == 1
            )
        for project, action in ((contexts[0].project_id, "auth.login"), (None, "secure_query.access_denied")):
            with psycopg.connect(**auth_database.config.conninfo()) as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(
                        "INSERT INTO public.audit_events(event_id,timestamp,request_id,actor_id,project_id,action,resource_type,decision,execution_status,duration_ms,mock,event_fingerprint) "
                        "VALUES(%s,now(),%s,%s,%s,%s,'session','allowed','completed',0,false,%s)",
                        (uuid4(), uuid4(), user.user_id, project, action, "f" * 64),
                    )
        # Even a mistaken project binding must not let the auth login write
        # project audit through the permissive PUBLIC worker policy.
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "INSERT INTO paperpilot_business.project_logins VALUES (%s,%s,%s)",
                (role, contexts[0].user_id, contexts[0].project_id),
            )
        try:
            with psycopg.connect(**auth_database.config.conninfo()) as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(
                        "INSERT INTO public.audit_events(event_id,timestamp,request_id,actor_id,project_id,action,resource_type,decision,execution_status,duration_ms,mock,event_fingerprint) "
                        "VALUES(%s,now(),%s,%s,%s,'auth.login','session','allowed','completed',0,false,%s)",
                        (uuid4(), uuid4(), user.user_id, contexts[0].project_id, "f" * 64),
                    )
        finally:
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute("DELETE FROM paperpilot_business.project_logins WHERE login_role=%s", (role,))
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("REVOKE INSERT ON public.audit_events FROM {}").format(sql.Identifier(role))
            )
        with pytest.raises(AppError) as error:
            sessions.revoke(token, uuid4(), user.user_id)
        assert error.value.code == "AUDIT_UNAVAILABLE"
        with pytest.raises(AppError) as error:
            sessions.create(user.user_id, login_password, uuid4())
        assert error.value.code == "AUDIT_UNAVAILABLE"
        with psycopg.connect(**config.conninfo()) as admin:
            assert (
                admin.execute(
                    "SELECT count(*) FROM public.auth_sessions WHERE user_id=%s AND revoked_at IS NULL",
                    (user.user_id,),
                ).fetchone()[0]
                == 1
            )
        assert sessions.current(token).user_id == user.user_id
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(sql.SQL("GRANT INSERT ON public.audit_events TO {}").format(sql.Identifier(role)))
        sessions.revoke(token, uuid4(), user.user_id)
        with pytest.raises(AppError):
            sessions.current(token)

        auth_database.set_transaction_guard(
            CandidateAuthAuditGuard(
                trusted_audit_owner=config.user,
                trusted_binding_owner=readers[0].config.function_owner,
            )
        )
        with auth_database.transaction():
            pass
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("ALTER POLICY auth_audit_scope_cap ON public.audit_events WITH CHECK (true)")
        with pytest.raises(AppError) as error:
            sessions.authenticate("alice", login_password)
        assert error.value.code == "AUDIT_UNAVAILABLE"
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "ALTER POLICY auth_audit_scope_cap ON public.audit_events WITH CHECK "
                "(project_id IS NULL AND resource_type='session' AND action IN "
                "('auth.login','auth.logout','auth.session.invalid','auth.login.rate_limited') "
                "AND execution_status='completed' AND mock=false)"
            )
            admin.execute(
                "INSERT INTO paperpilot_business.project_logins VALUES (%s,%s,%s)",
                (role, contexts[0].user_id, contexts[0].project_id),
            )
        with pytest.raises(AppError):
            sessions.authenticate("alice", login_password)
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("DELETE FROM paperpilot_business.project_logins WHERE login_role=%s", (role,))
        assert sessions.authenticate("alice", login_password) == user.user_id

        system_role = "paperpilot_system_audit_writer"
        system_password = secrets.token_urlsafe(32)
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (system_role,)).fetchone():
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT").format(sql.Identifier(system_role)))
            admin.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(system_role), sql.Literal(system_password)
                )
            )
            admin.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public,paperpilot_business,paperpilot_audit TO {}").format(
                    sql.Identifier(system_role)
                )
            )
            admin.execute(
                sql.SQL(
                    "GRANT EXECUTE ON FUNCTION paperpilot_business.bound_project(),paperpilot_business.bound_identity(uuid,uuid) TO {}"
                ).format(sql.Identifier(system_role))
            )
            admin.execute(
                sql.SQL("GRANT INSERT ON public.audit_events TO {}").format(sql.Identifier(system_role))
            )
            admin.execute(
                (migrations / "015_candidate_system_audit_rls.sql").read_text(encoding="utf-8-sig"),
                prepare=False,
            )
        system_writer = ScopedSystemAuditWriter(
            replace(config, user=system_role, password=system_password),
            trusted_owner=config.user,
            trusted_binding_function_owner=readers[0].config.function_owner,
        )
        assert system_writer.ready()
        with psycopg.connect(**system_writer.config.conninfo()) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT * FROM public.audit_events")
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "INSERT INTO paperpilot_business.project_logins VALUES (%s,%s,%s)",
                (system_role, contexts[0].user_id, contexts[0].project_id),
            )
        try:
            with psycopg.connect(**system_writer.config.conninfo()) as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(
                        "INSERT INTO public.audit_events(event_id,timestamp,request_id,actor_id,project_id,action,resource_type,decision,execution_status,duration_ms,mock,event_fingerprint) "
                        "VALUES(%s,now(),%s,%s,%s,'auth.login','session','allowed','pending',0,false,%s)",
                        (uuid4(), uuid4(), user.user_id, contexts[0].project_id, "f" * 64),
                    )
        finally:
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute(
                    "DELETE FROM paperpilot_business.project_logins WHERE login_role=%s", (system_role,)
                )
        request_binding = compose_data_binding(config, DataSettings())
        app = create_app(
            Settings(
                environment="test",
                a_mode="persistent",
                auth_mode="credentials",
                allowed_origins=("http://trusted.test",),
            ),
            data_binding=request_binding,
            scoped_system_audit=system_writer,
        )
        # The separate auth DB is exercised above; this explicit test injection
        # retains it for HTTP without claiming full AuthenticationBoundary readiness.
        app.state.sessions = sessions
        with TestClient(app, base_url="http://trusted.test") as client:
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute("ALTER POLICY system_audit_scope_cap ON public.audit_events WITH CHECK (true)")
            assert client.get("/api/v1/health").status_code == 503
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute(
                    "ALTER POLICY system_audit_scope_cap ON public.audit_events WITH CHECK "
                    "(paperpilot_audit.system_event_allowed(project_id, action, resource_type, decision, execution_status, mock))"
                )
                admin.execute(
                    "ALTER FUNCTION paperpilot_audit.system_event_allowed(uuid,text,text,text,text,boolean) VOLATILE"
                )
            assert client.get("/api/v1/health").status_code == 503
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute(
                    "ALTER FUNCTION paperpilot_audit.system_event_allowed(uuid,text,text,text,text,boolean) IMMUTABLE"
                )
            assert client.get("/api/v1/health").status_code == 200
            csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
            headers = {"Origin": "http://trusted.test", "X-CSRF-Token": csrf}
            request_id = str(uuid4())
            rejected = client.post(
                "/api/v1/auth/login",
                headers={**headers, "X-Request-ID": request_id},
                json={"username": "alice", "password": "incorrect"},
            )
            assert rejected.status_code == 401
            anonymous_id = str(uuid4())
            denied = client.post(
                f"/api/v1/projects/{contexts[0].project_id}/secure-queries",
                headers={**headers, "X-Request-ID": anonymous_id},
                json={"question": "How many papers?"},
            )
            assert denied.status_code == 401
            with psycopg.connect(**config.conninfo()) as admin:
                rows = admin.execute(
                    "SELECT action,project_id,decision FROM public.audit_events WHERE request_id IN (%s,%s)",
                    (request_id, anonymous_id),
                ).fetchall()
                assert {row[0] for row in rows} == {"auth.login", "secure_query.access_denied"}
                assert all(row[1] is None and row[2] == "rejected" for row in rows)
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute(
                    sql.SQL("REVOKE INSERT ON public.audit_events FROM {}").format(
                        sql.Identifier(system_role)
                    )
                )
            blocked = client.post(
                "/api/v1/auth/login", headers=headers, json={"username": "alice", "password": login_password}
            )
            assert blocked.status_code == 503 and "pp_session" not in blocked.cookies
            assert client.get("/api/v1/health").status_code == 503
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute(
                    sql.SQL("GRANT INSERT ON public.audit_events TO {}").format(sql.Identifier(system_role))
                )
            assert client.get("/api/v1/health").status_code == 200
            logged_in = client.post(
                "/api/v1/auth/login", headers=headers, json={"username": "alice", "password": login_password}
            )
            assert logged_in.status_code == 200
            assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    finally:
        auth_database.close()


def test_scoped_writer_import_idempotency_and_actor_isolation(scoped_business):
    config, _, contexts, _, readers, _, (role, password, ctx) = scoped_business
    migration = (
        Path(__file__).resolve().parents[1]
        / "app/security/migrations/006_candidate_business_actor_checks.sql"
    ).read_text(encoding="utf-8-sig")
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(migration, prepare=False)
        admin.execute(sql.SQL("REVOKE INSERT,UPDATE ON public.papers FROM {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,username,display_name) ON public.users TO {}").format(
                sql.Identifier(role)
            )
        )
        for table in PROJECT_TABLES:
            admin.execute(
                sql.SQL("GRANT SELECT ON public.{} TO {}").format(sql.Identifier(table), sql.Identifier(role))
            )
        for table, columns in INSERT_COLUMNS.items():
            admin.execute(
                sql.SQL("GRANT INSERT ({}) ON public.{} TO {}").format(
                    sql.SQL(",").join(map(sql.Identifier, sorted(columns))),
                    sql.Identifier(table),
                    sql.Identifier(role),
                )
            )
    writer = ScopedBusinessWriter(
        ScopedBusinessConfig(
            replace(config, user=role, password=password),
            ctx.user_id,
            ctx.project_id,
            readers[0].config.function_owner,
            config.user,
        )
    )
    try:
        with psycopg.connect(**writer.config.connection.conninfo(), row_factory=dict_row) as conn:
            writer._verify_database(conn)
        assert readers[0].ready()
        paper_id = uuid4()
        file = FileReference(f"{paper_id}.pdf", paper_id, ctx.project_id)
        first = writer.import_paper(ctx, "Scoped upload", 2024, "a" * 64, file, "same-key")
        again = writer.import_paper(ctx, "Scoped upload", 2024, "a" * 64, file, "same-key")
        assert first[0].paper_id == again[0].paper_id == paper_id
        assert first[1].task_id == again[1].task_id
        question_task = writer.create_task(ctx, "evidence", research_payload(paper_id), "evidence-key")
        assert (
            writer.create_task(ctx, "evidence", research_payload(paper_id), "evidence-key").task_id
            == question_task.task_id
        )
        with pytest.raises(AppError) as denied:
            writer.create_task(contexts[1], "evidence", {"paper_ids": [paper_id]}, "other-project")
        assert denied.value.status == 404
        with psycopg.connect(**writer.config.connection.conninfo()) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM public.project_papers WHERE paper_id=%s", (paper_id,)
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM public.tasks WHERE task_id=%s", (first[1].task_id,)
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM public.outbox_events WHERE project_id=%s AND actor_id=%s",
                    (ctx.project_id, ctx.user_id),
                ).fetchone()[0]
                >= 2
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO public.outbox_events(event_id,project_id,actor_id,request_id,payload) VALUES(%s,%s,%s,%s,'{}')",
                    (uuid4(), ctx.project_id, contexts[1].user_id, uuid4()),
                )
        rollback_id = uuid4()
        rollback_ctx = Context(ctx.user_id, ctx.project_id, uuid4())
        rollback_file = FileReference(f"{rollback_id}.pdf", rollback_id, ctx.project_id)

        def fail_commit(_operation, _conn):
            raise RuntimeError("simulated transaction rollback")

        writer._data._before_commit = fail_commit
        with pytest.raises(RuntimeError, match="simulated transaction rollback"):
            writer.import_paper(rollback_ctx, "Rollback", 2024, "b" * 64, rollback_file, "rollback-key")
        writer._data._before_commit = None
        with psycopg.connect(**writer.config.connection.conninfo()) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM public.project_papers WHERE paper_id=%s", (rollback_id,)
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM public.idempotency_records WHERE key='rollback-key'"
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM public.outbox_events WHERE request_id=%s",
                    (rollback_ctx.request_id,),
                ).fetchone()[0]
                == 0
            )
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "ALTER POLICY project_identity ON public.tasks WITH CHECK "
                "(project_id=paperpilot_business.bound_project())"
            )
        with pytest.raises(AppError):
            writer.ready()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "ALTER POLICY project_identity ON public.tasks WITH CHECK "
                "(project_id=paperpilot_business.bound_project() "
                "AND paperpilot_business.bound_identity(actor_id,project_id))"
            )
            admin.execute(
                sql.SQL("GRANT INSERT (published_at) ON public.outbox_events TO {}").format(
                    sql.Identifier(role)
                )
            )
        with pytest.raises(AppError):
            writer.ready()
    finally:
        writer.close()


def test_scoped_report_versions_and_author_integrity(scoped_business):
    config, data, contexts, rows, readers, _, (role, password, ctx) = scoped_business
    bob = data.user_by_id(contexts[1].user_id)
    data.add_member(ctx, bob.username)
    bob_ctx = Context(bob.user_id, ctx.project_id, uuid4())
    source = rows[0][2].sources
    first = data.save_report(
        bob_ctx, ReportSave(title="Shared", research_question="Q", body="First", sources=source), "bob-report"
    )
    migrations = Path(__file__).resolve().parents[1] / "app/security/migrations"
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        for filename in ("006_candidate_business_actor_checks.sql", "007_candidate_report_actor_checks.sql"):
            admin.execute((migrations / filename).read_text(encoding="utf-8-sig"), prepare=False)
        admin.execute(sql.SQL("REVOKE INSERT,UPDATE ON public.papers FROM {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,username,display_name) ON public.users TO {}").format(
                sql.Identifier(role)
            )
        )
        for table in PROJECT_TABLES:
            admin.execute(
                sql.SQL("GRANT SELECT ON public.{} TO {}").format(sql.Identifier(table), sql.Identifier(role))
            )
        for table, columns in {**INSERT_COLUMNS, **REPORT_INSERT_COLUMNS}.items():
            admin.execute(
                sql.SQL("GRANT INSERT ({}) ON public.{} TO {}").format(
                    sql.SQL(",").join(map(sql.Identifier, sorted(columns))),
                    sql.Identifier(table),
                    sql.Identifier(role),
                )
            )
        for table, columns in REPORT_UPDATE_COLUMNS.items():
            admin.execute(
                sql.SQL("GRANT UPDATE ({}) ON public.{} TO {}").format(
                    sql.SQL(",").join(map(sql.Identifier, sorted(columns))),
                    sql.Identifier(table),
                    sql.Identifier(role),
                )
            )
    writer = ScopedReportWriter(
        ScopedBusinessConfig(
            replace(config, user=role, password=password),
            ctx.user_id,
            ctx.project_id,
            readers[0].config.function_owner,
            config.user,
        )
    )
    try:
        with psycopg.connect(**writer.config.connection.conninfo(), row_factory=dict_row) as conn:
            writer._verify_database(conn)
        assert readers[0].ready()
        new_data = ReportSave(
            title="Shared",
            research_question="Q",
            body="Second",
            sources=source,
            report_id=first.report_id,
            base_version_id=first.version_id,
        )
        saved = writer.save_report(ctx, new_data, "alice-edit")
        assert writer.save_report(ctx, new_data, "alice-edit").version_id == saved.version_id
        versions = writer.versions(ctx, first.report_id)
        assert [version.body for version in versions] == ["First", "Second"]
        assert [version.sources for version in versions] == [source, source]
        with pytest.raises(AppError) as conflict:
            writer.save_report(ctx, new_data, "stale-edit")
        assert conflict.value.code == "VERSION_CONFLICT"
        rolled_back_ctx = Context(ctx.user_id, ctx.project_id, uuid4())
        third_data = new_data.model_copy(update={"body": "Third", "base_version_id": saved.version_id})

        def fail_commit(_operation, _conn):
            raise RuntimeError("simulated report rollback")

        writer._data._before_commit = fail_commit
        with pytest.raises(RuntimeError, match="simulated report rollback"):
            writer.save_report(rolled_back_ctx, third_data, "report-rollback")
        writer._data._before_commit = None
        assert [v.body for v in writer.versions(ctx, first.report_id)] == ["First", "Second"]
        with psycopg.connect(**writer.config.connection.conninfo()) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM public.outbox_events WHERE request_id=%s",
                    (rolled_back_ctx.request_id,),
                ).fetchone()[0]
                == 0
            )
        other = ScopedReportWriter(writer.config)
        gate = Barrier(2)

        def concurrent_save(instance, key):
            gate.wait(timeout=10)
            try:
                return instance.save_report(ctx, third_data, key).number
            except AppError as exc:
                return exc.code

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(concurrent_save, writer, "race-one")
                b = pool.submit(concurrent_save, other, "race-two")
                assert {a.result(timeout=15), b.result(timeout=15)} == {3, "VERSION_CONFLICT"}
        finally:
            other.close()
        assert [v.body for v in writer.versions(ctx, first.report_id)] == ["First", "Second", "Third"]
        with psycopg.connect(**writer.config.connection.conninfo()) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO public.reports(report_id,project_id,created_by,current_version_id,created_at) "
                    "VALUES(%s,%s,%s,%s,now())",
                    (uuid4(), ctx.project_id, bob.user_id, uuid4()),
                )
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("DROP POLICY report_insert_actor ON public.report_versions")
        with pytest.raises(AppError):
            writer.ready()
    finally:
        writer.close()


def test_report_api_uses_scoped_sources_and_writer(scoped_business, monkeypatch):  # noqa: F811
    config, data, contexts, rows, readers, _, (role, password, ctx) = scoped_business
    migrations = Path(__file__).resolve().parents[1] / "app/security/migrations"
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        for name in ("006_candidate_business_actor_checks.sql", "007_candidate_report_actor_checks.sql"):
            admin.execute((migrations / name).read_text(encoding="utf-8-sig"), prepare=False)
        admin.execute(sql.SQL("REVOKE INSERT,UPDATE ON public.papers FROM {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,username,display_name) ON public.users TO {}").format(
                sql.Identifier(role)
            )
        )
        for table in PROJECT_TABLES:
            admin.execute(
                sql.SQL("GRANT SELECT ON public.{} TO {}").format(sql.Identifier(table), sql.Identifier(role))
            )
        for table, columns in {**INSERT_COLUMNS, **REPORT_INSERT_COLUMNS}.items():
            admin.execute(
                sql.SQL("GRANT INSERT ({}) ON public.{} TO {}").format(
                    sql.SQL(",").join(map(sql.Identifier, sorted(columns))),
                    sql.Identifier(table),
                    sql.Identifier(role),
                )
            )
        for table, columns in REPORT_UPDATE_COLUMNS.items():
            admin.execute(
                sql.SQL("GRANT UPDATE ({}) ON public.{} TO {}").format(
                    sql.SQL(",").join(map(sql.Identifier, sorted(columns))),
                    sql.Identifier(table),
                    sql.Identifier(role),
                )
            )
    config = ScopedBusinessConfig(
        replace(config, user=role, password=password),
        ctx.user_id,
        ctx.project_id,
        readers[0].config.function_owner,
        config.user,
    )
    writer = ScopedReportWriter(config)
    app = create_app(Settings(environment="test"), scoped_business_reader=ScopedBusinessRouter([writer]))
    mock_data = app.state.adapters.data
    monkeypatch.setattr(mock_data, "user_by_id", data.user_by_id)
    monkeypatch.setattr(mock_data, "role", data.role)
    monkeypatch.setattr(mock_data, "append_audit", lambda _event: None)
    monkeypatch.setattr(mock_data, "sources", lambda *_args: pytest.fail("unscoped source fallback"))
    monkeypatch.setattr(mock_data, "save_report", lambda *_args: pytest.fail("unscoped report fallback"))
    previous = rows[0][2]
    payload = ReportSave(
        title="Updated",
        research_question="Q",
        body="Updated [1]",
        sources=previous.sources,
        report_id=previous.report_id,
        base_version_id=previous.version_id,
    ).model_dump(mode="json")
    root = f"/api/v1/projects/{ctx.project_id}"
    headers = {"X-PaperPilot-Client": "web", "Idempotency-Key": "report-api"}
    with TestClient(app) as client:
        assert "scoped_business_report_pilot" in client.get("/api/v1/health").json()["limitations"]
        assert client.post(root + "/reports", json=payload, headers=headers).status_code == 401
        client.cookies.set("pp_session", app.state.sessions.create(ctx.user_id), path="/api/v1")

        def fail_audit(_event):
            raise RuntimeError("controlled audit failure")

        monkeypatch.setattr(mock_data, "append_audit", fail_audit)
        assert client.post(root + "/reports", json=payload, headers=headers).status_code == 503
        assert len(client.get(root + f"/reports/{previous.report_id}/versions").json()["items"]) == 1
        audit_calls = []

        def fail_after_commit(event):
            audit_calls.append(event)
            if len(audit_calls) == 2:
                raise RuntimeError("controlled post-commit audit failure")

        monkeypatch.setattr(mock_data, "append_audit", fail_after_commit)
        assert client.post(root + "/reports", json=payload, headers=headers).status_code == 503
        assert len(client.get(root + f"/reports/{previous.report_id}/versions").json()["items"]) == 2
        monkeypatch.setattr(mock_data, "append_audit", lambda _event: None)
        result = client.post(root + "/reports", json=payload, headers=headers)
        assert result.status_code == 201, result.text
        replay = client.post(root + "/reports", json=payload, headers=headers)
        assert replay.status_code == 201
        assert replay.json()["version_id"] == result.json()["version_id"]
        versions = client.get(root + f"/reports/{previous.report_id}/versions")
        assert versions.status_code == 200
        assert [v["body"] for v in versions.json()["items"]] == [previous.body, "Updated [1]"]
        assert versions.json()["items"][1]["sources"] == payload["sources"]
        forged = {**payload, "sources": [rows[1][2].sources[0].model_dump(mode="json")]}
        assert client.post(
            root + "/reports", json=forged, headers={**headers, "Idempotency-Key": "forged"}
        ).status_code in (404, 502)
        with psycopg.connect(**config.connection.conninfo()) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM public.report_versions WHERE project_id=%s",
                    (contexts[1].project_id,),
                ).fetchone()[0]
                == 0
            )


def test_scoped_worker_claims_only_bound_actor_and_project(scoped_business):
    config, data, contexts, rows, readers, _, (role, password, ctx) = scoped_business
    bob = data.user_by_id(contexts[1].user_id)
    data.add_member(ctx, bob.username)
    bob_ctx = Context(bob.user_id, ctx.project_id, uuid4())
    payload = research_payload(rows[0][0].paper_id)
    other_task = data.create_task(bob_ctx, "evidence", payload, "other-actor")
    own_task = data.create_task(ctx, "evidence", payload, "own-actor")
    migrations = Path(__file__).resolve().parents[1] / "app/security/migrations"
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        for filename in ("006_candidate_business_actor_checks.sql", "008_candidate_worker_actor_checks.sql"):
            admin.execute((migrations / filename).read_text(encoding="utf-8-sig"), prepare=False)
        admin.execute(sql.SQL("REVOKE INSERT,UPDATE ON public.papers FROM {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,username,display_name) ON public.users TO {}").format(
                sql.Identifier(role)
            )
        )
        for table in PROJECT_TABLES:
            admin.execute(
                sql.SQL("GRANT SELECT ON public.{} TO {}").format(sql.Identifier(table), sql.Identifier(role))
            )
        for permission, profile in (("INSERT", WORKER_INSERT_COLUMNS), ("UPDATE", WORKER_UPDATE_COLUMNS)):
            for table, columns in profile.items():
                admin.execute(
                    sql.SQL("GRANT {} ({}) ON public.{} TO {}").format(
                        sql.SQL(permission),
                        sql.SQL(",").join(map(sql.Identifier, sorted(columns))),
                        sql.Identifier(table),
                        sql.Identifier(role),
                    )
                )
    config_worker = ScopedBusinessConfig(
        replace(config, user=role, password=password),
        ctx.user_id,
        ctx.project_id,
        readers[0].config.function_owner,
        config.user,
    )
    workers = [ScopedClaimWorker(config_worker), ScopedClaimWorker(config_worker)]
    gate = Barrier(2)

    def claim(worker):
        gate.wait(timeout=10)
        return worker.claim_job()

    try:
        assert all(worker.ready() for worker in workers)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(claim, workers[0])
            b = pool.submit(claim, workers[1])
            jobs = [a.result(timeout=15), b.result(timeout=15)]
        claimed = [job for job in jobs if job is not None]
        assert len(claimed) == 1 and claimed[0].task.task_id == own_task.task_id
        assert claimed[0].task.attempts == 1
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "UPDATE public.task_attempts SET claimed_at='1999-12-31T00:00:00+00:00', "
                "lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=%s AND attempt_no=1",
                (own_task.task_id,),
            )
        recovered = workers[1].claim_job()
        assert recovered.task.task_id == own_task.task_id and recovered.task.attempts == 2
        with psycopg.connect(**config_worker.connection.conninfo()) as conn:
            assert conn.execute(
                "SELECT status FROM public.task_attempts WHERE task_id=%s ORDER BY attempt_no",
                (own_task.task_id,),
            ).fetchall() == [("expired",), ("processing",)]
            assert (
                conn.execute(
                    "SELECT status FROM public.tasks WHERE task_id=%s", (other_task.task_id,)
                ).fetchone()[0]
                == "pending"
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "UPDATE public.tasks SET status='processing',attempts=1 WHERE task_id=%s",
                    (other_task.task_id,),
                )
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("DELETE FROM paperpilot_business.project_logins WHERE login_role=%s", (role,))
        with pytest.raises(AppError):
            workers[0].claim_job()
    finally:
        for worker in workers:
            worker.close()


def test_scoped_worker_failure_and_stale_attempt(scoped_business):
    config, data, _, rows, readers, _, (role, password, ctx) = scoped_business
    evidence = data.create_task(ctx, "evidence", research_payload(rows[0][0].paper_id), "failure-evidence")
    paper_id = uuid4()
    paper, process = data.import_paper(
        ctx,
        "Pending",
        2024,
        "d" * 64,
        FileReference(f"{paper_id}.pdf", paper_id, ctx.project_id),
        "failure-process",
    )
    migrations = Path(__file__).resolve().parents[1] / "app/security/migrations"
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        for filename in ("006_candidate_business_actor_checks.sql", "008_candidate_worker_actor_checks.sql"):
            admin.execute((migrations / filename).read_text(encoding="utf-8-sig"), prepare=False)
        admin.execute(sql.SQL("REVOKE INSERT,UPDATE ON public.papers FROM {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,username,display_name) ON public.users TO {}").format(
                sql.Identifier(role)
            )
        )
        for table in PROJECT_TABLES:
            admin.execute(
                sql.SQL("GRANT SELECT ON public.{} TO {}").format(sql.Identifier(table), sql.Identifier(role))
            )
        for permission, profile in (("INSERT", FAILURE_INSERT_COLUMNS), ("UPDATE", FAILURE_UPDATE_COLUMNS)):
            for table, columns in profile.items():
                admin.execute(
                    sql.SQL("GRANT {} ({}) ON public.{} TO {}").format(
                        sql.SQL(permission),
                        sql.SQL(",").join(map(sql.Identifier, sorted(columns))),
                        sql.Identifier(table),
                        sql.Identifier(role),
                    )
                )
    worker = ScopedFailureWorker(
        ScopedBusinessConfig(
            replace(config, user=role, password=password),
            ctx.user_id,
            ctx.project_id,
            readers[0].config.function_owner,
            config.user,
        )
    )
    try:
        first = worker.claim_job()
        assert first.task.task_id == evidence.task_id and first.task.attempts == 1
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "UPDATE public.task_attempts SET claimed_at='1999-12-31T00:00:00+00:00', "
                "lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=%s AND attempt_no=1",
                (evidence.task_id,),
            )
        second = worker.claim_job()
        assert second.task.task_id == evidence.task_id and second.task.attempts == 2
        with pytest.raises(AppError) as stale:
            worker.fail_job(first, "AI_FAILURE", "Temporary failure")
        assert stale.value.code == "TASK_STATE_CONFLICT"
        worker.fail_job(second, "AI_FAILURE", "Temporary failure")
        worker.fail_job(second, "AI_FAILURE", "Temporary failure")
        assert worker.task(ctx, evidence.task_id).status == "failed"
        with psycopg.connect(**worker.config.connection.conninfo()) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM public.outbox_events WHERE request_id=%s AND payload->>'action'='fail_job'",
                    (second.context.request_id,),
                ).fetchone()[0]
                == 1
            )
        process_job = worker.claim_job()
        assert process_job.task.task_id == process.task_id

        def fail_commit(_operation, _conn):
            raise RuntimeError("simulated failure rollback")

        worker._data._before_commit = fail_commit
        with pytest.raises(RuntimeError, match="simulated failure rollback"):
            worker.fail_job(process_job, "PROCESS_FAILURE", "Cannot process")
        worker._data._before_commit = None
        assert worker.task(ctx, process.task_id).status == "processing"
        worker.fail_job(process_job, "PROCESS_FAILURE", "Cannot process")
        assert worker.task(ctx, process.task_id).status == "failed"
        assert worker.paper(ctx, paper.paper_id).status == "failed"
        success_id = uuid4()
        success_paper, success_task = data.import_paper(
            ctx,
            "Process result",
            2024,
            "e" * 64,
            FileReference(f"{success_id}.pdf", success_id, ctx.project_id),
            "success-process",
        )
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                (migrations / "009_candidate_worker_result_checks.sql").read_text(encoding="utf-8-sig"),
                prepare=False,
            )
            for signature in (
                "worker_has_lease(uuid,uuid,integer)",
                "worker_has_result(uuid,uuid)",
                "worker_has_process(uuid,uuid)",
            ):
                admin.execute(
                    sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_business.{} TO {}").format(
                        sql.SQL(signature), sql.Identifier(role)
                    )
                )
            for permission, profile in (("INSERT", RESULT_INSERT_COLUMNS), ("UPDATE", RESULT_UPDATE_COLUMNS)):
                for table, columns in profile.items():
                    admin.execute(
                        sql.SQL("GRANT {} ({}) ON public.{} TO {}").format(
                            sql.SQL(permission),
                            sql.SQL(",").join(map(sql.Identifier, sorted(columns))),
                            sql.Identifier(table),
                            sql.Identifier(role),
                        )
                    )
        result_worker = ScopedResultWorker(worker.config)
        try:
            with psycopg.connect(**result_worker.config.connection.conninfo(), row_factory=dict_row) as conn:
                result_worker._verify_database(conn)
            old = result_worker.claim_job()
            assert old.task.task_id == success_task.task_id
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute(
                    "UPDATE public.task_attempts SET claimed_at='1999-12-31T00:00:00+00:00', "
                    "lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=%s AND attempt_no=1",
                    (success_task.task_id,),
                )
            current = result_worker.claim_job()
            result = processing_result(success_paper.paper_id)
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute("UPDATE public.users SET disabled=true WHERE user_id=%s", (ctx.user_id,))
            with psycopg.connect(**result_worker.config.connection.conninfo()) as conn:
                assert conn.execute("SELECT count(*) FROM public.project_papers").fetchone()[0] == 0
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(
                        "INSERT INTO public.task_results(task_id,project_id,kind,committed_attempt,paper_id,"
                        "evidence_id,payload,result_fingerprint,created_at) VALUES(%s,%s,'process',%s,%s,NULL,'{}',%s,now())",
                        (
                            success_task.task_id,
                            ctx.project_id,
                            current.task.attempts,
                            success_paper.paper_id,
                            "e" * 64,
                        ),
                    )
            for operation in (
                result_worker.ready,
                result_worker.claim_job,
                lambda: result_worker.complete_job(current, result),
                lambda: result_worker.fail_job(current, "STOPPED", "Account disabled"),
            ):
                with pytest.raises(AppError):
                    operation()
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute("UPDATE public.users SET disabled=false WHERE user_id=%s", (ctx.user_id,))
            assert result_worker.ready()
            with psycopg.connect(**result_worker.config.connection.conninfo()) as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(
                        "INSERT INTO public.task_results(task_id,project_id,kind,committed_attempt,paper_id,"
                        "evidence_id,payload,result_fingerprint,created_at) VALUES(%s,%s,'process',1,%s,NULL,'{}',%s,now())",
                        (success_task.task_id, ctx.project_id, success_paper.paper_id, "f" * 64),
                    )
            with pytest.raises(AppError) as stale_result:
                result_worker.complete_job(old, result)
            assert stale_result.value.code == "TASK_STATE_CONFLICT"
            result_worker._data._before_commit = fail_commit
            with pytest.raises(RuntimeError, match="simulated failure rollback"):
                result_worker.complete_job(current, result)
            result_worker._data._before_commit = None
            assert result_worker.task(ctx, success_task.task_id).status == "processing"
            assert result_worker.sources(ctx, success_paper.paper_id) == []
            result_worker.complete_job(current, result)
            result_worker.complete_job(current, result)
            assert result_worker.task(ctx, success_task.task_id).status == "succeeded"
            assert result_worker.paper(ctx, success_paper.paper_id).status == "searchable"
            assert result_worker.sources(ctx, success_paper.paper_id) == result.sources
            evidence_task = data.create_task(
                ctx, "evidence", research_payload(success_paper.paper_id), "result-evidence"
            )
            evidence_job = result_worker.claim_job()
            assert evidence_job.task.task_id == evidence_task.task_id
            evidence_output = evidence_result(result)
            result_worker.complete_job(evidence_job, evidence_output)
            assert result_worker.task(ctx, evidence_task.task_id).status == "succeeded"
            for kind, output in (
                (
                    "answer",
                    AnswerResult(
                        answer="Fixture",
                        citation_ids=["1"],
                        sources=result.sources,
                        insufficient_evidence=False,
                    ),
                ),
                ("draft", DraftResult(body="Fixture draft", sources=result.sources)),
            ):
                task = data.create_task(ctx, kind, research_payload(success_paper.paper_id), "result-" + kind)
                job = result_worker.claim_job()
                assert job.task.task_id == task.task_id
                result_worker.complete_job(job, output)
                assert result_worker.task(ctx, task.task_id).status == "succeeded"
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute(
                    "ALTER FUNCTION paperpilot_business.worker_has_lease(uuid,uuid,integer) SECURITY DEFINER"
                )
            with pytest.raises(AppError):
                result_worker.ready()
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute(
                    "ALTER FUNCTION paperpilot_business.worker_has_lease(uuid,uuid,integer) SECURITY INVOKER"
                )
                admin.execute("DROP POLICY worker_result_actor ON public.task_results")
            with pytest.raises(AppError):
                result_worker.ready()
        finally:
            result_worker.close()
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("DELETE FROM paperpilot_business.project_logins WHERE login_role=%s", (role,))
        with pytest.raises(AppError):
            worker.fail_job(process_job, "PROCESS_FAILURE", "Cannot process")
    finally:
        worker.close()
