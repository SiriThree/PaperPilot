"""Project bootstrap identity candidate on a disposable PostgreSQL server."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import secrets
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from app.adapters.ports import Context
from app.data.scoped_business import PROJECT_TABLES, ScopedBusinessConfig, ScopedBusinessReader
from app.data.scoped_project_creator import (
    OWNER,
    OWNER_INSERT,
    ProjectCreatorConfig,
    ScopedProjectCreator,
    ScopedReplayProjectCreator,
)
from app.errors import AppError
from app.schemas import ProjectCreate
from test_scoped_business_postgres import scoped_business  # noqa: F401
from test_phase5a_persistent import database  # noqa: F401

pytestmark = pytest.mark.real_database


@pytest.fixture
def bootstrap(scoped_business):  # noqa: F811
    config, _, contexts, _, readers, _, _ = scoped_business
    role = "bootstrap_login_" + uuid4().hex[:10]
    secret = secrets.token_urlsafe(32)
    source = (
        Path(__file__).resolve().parents[1] / "app/security/migrations/010_candidate_project_bootstrap.sql"
    ).read_text(encoding="utf-8-sig")
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        # Tests use separate databases in the same disposable cluster. Reuse only
        # the fixed NOLOGIN owner created by an earlier test in that container.
        if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (OWNER,)).fetchone():
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
                ).format(sql.Identifier(OWNER))
            )
        admin.execute(source, prepare=False)
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(role), sql.Literal(secret))
        )
        admin.execute(
            "INSERT INTO paperpilot_business.bootstrap_logins VALUES(%s,%s)", (role, contexts[0].user_id)
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public,paperpilot_business TO {}").format(sql.Identifier(OWNER))
        )
        admin.execute(sql.SQL("GRANT USAGE ON SCHEMA paperpilot_business TO {}").format(sql.Identifier(role)))
        admin.execute(
            sql.SQL("GRANT CREATE ON SCHEMA paperpilot_business TO {}").format(sql.Identifier(OWNER))
        )
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,disabled) ON public.users TO {}").format(sql.Identifier(OWNER))
        )
        admin.execute(
            sql.SQL("GRANT SELECT (login_role,user_id) ON paperpilot_business.bootstrap_logins TO {}").format(
                sql.Identifier(OWNER)
            )
        )
        for table, columns in OWNER_INSERT.items():
            admin.execute(
                sql.SQL("GRANT INSERT ({}) ON public.{} TO {}").format(
                    sql.SQL(",").join(map(sql.Identifier, sorted(columns))),
                    sql.Identifier(table),
                    sql.Identifier(OWNER),
                )
            )
        admin.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION paperpilot_business.bound_project(),paperpilot_business.bound_identity(uuid,uuid) TO {}"
            ).format(sql.Identifier(OWNER))
        )
        for signature in ("bootstrap_user()", "bootstrap_matches(uuid)", "bootstrap_create(text,text,uuid)"):
            admin.execute(
                sql.SQL("ALTER FUNCTION paperpilot_business.{} OWNER TO {}").format(
                    sql.SQL(signature), sql.Identifier(OWNER)
                )
            )
        admin.execute(
            sql.SQL("REVOKE CREATE ON SCHEMA paperpilot_business FROM {}").format(sql.Identifier(OWNER))
        )
        admin.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION paperpilot_business.bootstrap_matches(uuid),paperpilot_business.bootstrap_create(text,text,uuid) TO {}"
            ).format(sql.Identifier(role))
        )
    creator = ScopedProjectCreator(
        ProjectCreatorConfig(
            replace(config, user=role, password=secret),
            contexts[0].user_id,
            config.user,
            readers[0].config.function_owner,
        )
    )
    try:
        yield config, contexts, role, creator
    finally:
        creator.close()


def test_bootstrap_creates_project_membership_and_event_atomically(bootstrap):
    config, contexts, role, creator = bootstrap
    with psycopg.connect(**creator.config.connection.conninfo(), row_factory=dict_row) as conn:
        creator._verify_database(conn)
    request_id = uuid4()
    project = creator.create_project(
        contexts[0].user_id, ProjectCreate(name="isolated bootstrap"), request_id
    )
    assert project.owner_id == contexts[0].user_id
    another = creator.create_project(
        contexts[0].user_id, ProjectCreate(name="isolated bootstrap"), request_id
    )
    assert another.project_id != project.project_id  # request_id is not an idempotency key
    with psycopg.connect(**config.conninfo()) as admin:
        assert admin.execute(
            "SELECT role FROM public.project_members WHERE project_id=%s AND user_id=%s",
            (project.project_id, contexts[0].user_id),
        ).fetchone() == ("owner",)
        assert (
            admin.execute(
                "SELECT count(*) FROM public.outbox_events WHERE project_id=%s AND request_id=%s",
                (project.project_id, request_id),
            ).fetchone()[0]
            == 1
        )
    with pytest.raises(AppError) as forged:
        creator.create_project(contexts[1].user_id, ProjectCreate(name="forged"), uuid4())
    assert forged.value.status == 404
    with psycopg.connect(**creator.config.connection.conninfo()) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM public.projects")
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "CREATE FUNCTION public.bootstrap_fixture_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture failure'; END $$"
        )
        admin.execute(
            "CREATE TRIGGER bootstrap_fixture_failure BEFORE INSERT ON public.outbox_events FOR EACH ROW EXECUTE FUNCTION public.bootstrap_fixture_failure()"
        )
    failed_request = uuid4()
    with pytest.raises(AppError):
        creator.create_project(contexts[0].user_id, ProjectCreate(name="rolled back"), failed_request)
    with psycopg.connect(**config.conninfo()) as admin:
        assert (
            admin.execute("SELECT count(*) FROM public.projects WHERE name='rolled back'").fetchone()[0] == 0
        )
        assert (
            admin.execute(
                "SELECT count(*) FROM public.outbox_events WHERE request_id=%s", (failed_request,)
            ).fetchone()[0]
            == 0
        )


def test_business_readers_and_project_bootstrap_coexist_fail_closed(bootstrap, scoped_business):  # noqa: F811
    config, _, _, creator = bootstrap
    _, _, contexts, rows, readers, _, _ = scoped_business
    assert creator.ready()
    for index, reader in enumerate(readers[:2]):
        assert reader.ready()
        assert reader.paper(contexts[index], rows[index][0].paper_id).paper_id == rows[index][0].paper_id
        with pytest.raises(AppError):
            reader.paper(contexts[index], rows[1 - index][0].paper_id)

    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("ALTER POLICY bootstrap_member_insert ON public.project_members TO PUBLIC")
    with pytest.raises(AppError):
        readers[0].ready()
    with pytest.raises(AppError):
        creator.ready()
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("ALTER POLICY bootstrap_member_insert ON public.project_members TO {}").format(
                sql.Identifier(OWNER)
            )
        )
        admin.execute("ALTER POLICY bootstrap_project_insert ON public.projects WITH CHECK (true)")
    with pytest.raises(AppError):
        readers[0].ready()
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "ALTER POLICY bootstrap_project_insert ON public.projects WITH CHECK "
            "(owner_id=paperpilot_business.bootstrap_user() AND owner_role='owner')"
        )
        admin.execute(
            sql.SQL("GRANT {} TO {}").format(
                sql.Identifier(OWNER), sql.Identifier(readers[0].config.connection.user)
            )
        )
    try:
        with pytest.raises(AppError):
            readers[0].ready()
        with pytest.raises(AppError):
            creator.ready()
    finally:
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("REVOKE {} FROM {}").format(
                    sql.Identifier(OWNER), sql.Identifier(readers[0].config.connection.user)
                )
            )
    assert readers[0].ready()
    assert creator.ready()


def test_new_project_identity_and_all_candidate_policies_coexist(bootstrap, scoped_business):  # noqa: F811
    config, contexts, _, creator = bootstrap
    _, _, _, rows, readers, _, _ = scoped_business
    migration_dir = Path(__file__).resolve().parents[1] / "app/security/migrations"
    role = "new_project_reader_" + uuid4().hex[:10]
    secret = secrets.token_urlsafe(32)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        for name in (
            "006_candidate_business_actor_checks.sql",
            "007_candidate_report_actor_checks.sql",
            "008_candidate_worker_actor_checks.sql",
            "009_candidate_worker_result_checks.sql",
        ):
            admin.execute((migration_dir / name).read_text(encoding="utf-8-sig"), prepare=False)
    assert creator.ready()
    assert readers[0].ready()
    project = creator.create_project(contexts[0].user_id, ProjectCreate(name="new isolated project"), uuid4())
    ctx = Context(contexts[0].user_id, project.project_id, uuid4())
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
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
            sql.SQL("GRANT SELECT (user_id,username,display_name) ON public.users TO {}").format(
                sql.Identifier(role)
            )
        )
        for table in PROJECT_TABLES:
            admin.execute(
                sql.SQL("GRANT SELECT ON public.{} TO {}").format(sql.Identifier(table), sql.Identifier(role))
            )
        admin.execute(
            "INSERT INTO paperpilot_business.project_logins VALUES (%s,%s,%s)",
            (role, ctx.user_id, ctx.project_id),
        )
    reader = ScopedBusinessReader(
        ScopedBusinessConfig(
            replace(config, user=role, password=secret),
            ctx.user_id,
            ctx.project_id,
            readers[0].config.function_owner,
            config.user,
        )
    )
    try:
        assert reader.ready()
        assert reader.papers(ctx) == []
        with psycopg.connect(**reader.config.connection.conninfo()) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM public.papers WHERE project_id=%s", (contexts[0].project_id,)
                ).fetchone()[0]
                == 0
            )
        with pytest.raises(AppError):
            reader.paper(ctx, rows[0][0].paper_id)
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute("DELETE FROM paperpilot_business.project_logins WHERE login_role=%s", (role,))
        with pytest.raises(AppError):
            reader.ready()
    finally:
        reader.close()


def test_bootstrap_rejects_disabled_revoked_and_privilege_drift(bootstrap):
    config, contexts, role, creator = bootstrap
    wrong = ScopedProjectCreator(replace(creator.config, user_id=contexts[1].user_id))
    try:
        with pytest.raises(AppError):
            wrong.ready()
    finally:
        wrong.close()
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL(
                "REVOKE EXECUTE ON FUNCTION paperpilot_business.bound_identity(uuid,uuid) FROM {}"
            ).format(sql.Identifier(OWNER))
        )
    with pytest.raises(AppError):
        creator.ready()
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION paperpilot_business.bound_identity(uuid,uuid) TO {}").format(
                sql.Identifier(OWNER)
            )
        )
        admin.execute("ALTER FUNCTION paperpilot_business.bound_identity(uuid,uuid) SECURITY INVOKER")
    with pytest.raises(AppError):
        creator.ready()
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("ALTER FUNCTION paperpilot_business.bound_identity(uuid,uuid) SECURITY DEFINER")
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("ALTER FUNCTION paperpilot_business.bound_project() SECURITY INVOKER")
    with pytest.raises(AppError):
        creator.ready()
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("ALTER FUNCTION paperpilot_business.bound_project() SECURITY DEFINER")
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("ALTER POLICY bootstrap_member_insert ON public.project_members WITH CHECK (true)")
    with pytest.raises(AppError):
        creator.ready()
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "ALTER POLICY bootstrap_member_insert ON public.project_members WITH CHECK "
            "(user_id=paperpilot_business.bootstrap_user() AND role='owner')"
        )
        admin.execute(sql.SQL("GRANT UPDATE (name) ON public.projects TO {}").format(sql.Identifier(OWNER)))
    with pytest.raises(AppError):
        creator.ready()
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("REVOKE UPDATE (name) ON public.projects FROM {}").format(sql.Identifier(OWNER))
        )
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("UPDATE public.users SET disabled=true WHERE user_id=%s", (contexts[0].user_id,))
    with pytest.raises(AppError):
        creator.ready()
    with psycopg.connect(**creator.config.connection.conninfo()) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "SELECT paperpilot_business.bootstrap_create(%s,%s,%s)",
                ("Disabled", "", uuid4()),
            )
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute("UPDATE public.users SET disabled=false WHERE user_id=%s", (contexts[0].user_id,))
        admin.execute(sql.SQL("GRANT SELECT ON public.projects TO {}").format(sql.Identifier(role)))
    with pytest.raises(AppError):
        creator.ready()
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(sql.SQL("REVOKE SELECT ON public.projects FROM {}").format(sql.Identifier(role)))
        admin.execute("DELETE FROM paperpilot_business.bootstrap_logins WHERE login_role=%s", (role,))
    with pytest.raises(AppError):
        creator.create_project(contexts[0].user_id, ProjectCreate(name="revoked"), uuid4())


@pytest.fixture
def replay_bootstrap(bootstrap):
    config, contexts, role, creator = bootstrap
    source = (
        Path(__file__).resolve().parents[1] / "app/security/migrations/011_candidate_project_replay.sql"
    ).read_text(encoding="utf-8-sig")
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(source, prepare=False)
        admin.execute(
            sql.SQL("GRANT CREATE ON SCHEMA paperpilot_business TO {}").format(sql.Identifier(OWNER))
        )
        admin.execute(
            sql.SQL(
                "GRANT SELECT (user_id,operation_key,project_id,name,description), INSERT (user_id,operation_key,project_id,name,description) ON paperpilot_business.bootstrap_requests TO {}"
            ).format(sql.Identifier(OWNER))
        )
        admin.execute(
            sql.SQL(
                "ALTER FUNCTION paperpilot_business.bootstrap_create_replay(text,text,uuid,text) OWNER TO {}"
            ).format(sql.Identifier(OWNER))
        )
        admin.execute(
            sql.SQL("REVOKE CREATE ON SCHEMA paperpilot_business FROM {}").format(sql.Identifier(OWNER))
        )
        admin.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION paperpilot_business.bootstrap_create_replay(text,text,uuid,text) TO {}"
            ).format(sql.Identifier(role))
        )
    replay = ScopedReplayProjectCreator(creator.config)
    try:
        yield config, contexts, role, replay
    finally:
        replay.close()


def test_project_replay_concurrent_and_restart(replay_bootstrap):
    config, contexts, _, replay = replay_bootstrap
    user = contexts[0].user_id
    data = ProjectCreate(name="replay fixture", description="test")
    with psycopg.connect(**replay.config.connection.conninfo(), row_factory=dict_row) as conn:
        replay._verify_database(conn)
    other = ScopedReplayProjectCreator(replay.config)
    gate = Barrier(2)

    def submit(instance):
        gate.wait(timeout=10)
        return instance.create_project_with_key(user, data, uuid4(), "same-op")

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(submit, replay)
            b = pool.submit(submit, other)
            assert a.result(timeout=15).project_id == b.result(timeout=15).project_id
    finally:
        other.close()
    recovered = ScopedReplayProjectCreator(replay.config)
    try:
        result = recovered.create_project_with_key(user, data, uuid4(), "same-op")
        same_request = uuid4()
        distinct1 = recovered.create_project_with_key(user, data, same_request, "different-op-1")
        distinct2 = recovered.create_project_with_key(user, data, same_request, "different-op-2")
        assert distinct1.project_id != distinct2.project_id
        with pytest.raises(AppError) as conflict:
            recovered.create_project_with_key(user, ProjectCreate(name="different"), uuid4(), "same-op")
        assert conflict.value.status == 409
    finally:
        recovered.close()
    with psycopg.connect(**config.conninfo()) as admin:
        assert (
            admin.execute(
                "SELECT count(*) FROM public.projects WHERE project_id=%s", (result.project_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            admin.execute(
                "SELECT count(*) FROM public.outbox_events WHERE project_id=%s", (result.project_id,)
            ).fetchone()[0]
            == 1
        )
    role_b = "bootstrap_login_" + uuid4().hex[:10]
    password_b = secrets.token_urlsafe(32)
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(role_b), sql.Literal(password_b))
        )
        admin.execute(
            "INSERT INTO paperpilot_business.bootstrap_logins VALUES(%s,%s)", (role_b, contexts[1].user_id)
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA paperpilot_business TO {}").format(sql.Identifier(role_b))
        )
        admin.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION paperpilot_business.bootstrap_matches(uuid),paperpilot_business.bootstrap_create(text,text,uuid),paperpilot_business.bootstrap_create_replay(text,text,uuid,text) TO {}"
            ).format(sql.Identifier(role_b))
        )
    other_user = ScopedReplayProjectCreator(
        replace(
            replay.config,
            connection=replace(config, user=role_b, password=password_b),
            user_id=contexts[1].user_id,
        )
    )
    try:
        theirs = other_user.create_project_with_key(contexts[1].user_id, data, uuid4(), "same-op")
        assert theirs.project_id != result.project_id and theirs.owner_id == contexts[1].user_id
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            admin.execute(
                "UPDATE paperpilot_business.bootstrap_logins SET user_id=%s WHERE login_role=%s",
                (user, role_b),
            )
        with pytest.raises(AppError):
            other_user.create_project_with_key(contexts[1].user_id, data, uuid4(), "same-op")
        rotated = ScopedReplayProjectCreator(replace(other_user.config, user_id=user))
        try:
            assert (
                rotated.create_project_with_key(user, data, uuid4(), "same-op").project_id
                == result.project_id
            )
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                admin.execute(
                    "DELETE FROM paperpilot_business.bootstrap_logins WHERE login_role=%s", (role_b,)
                )
            with pytest.raises(AppError):
                rotated.create_project_with_key(user, data, uuid4(), "same-op")
        finally:
            rotated.close()
    finally:
        other_user.close()


def test_project_replay_rollback_and_drift(replay_bootstrap):
    config, contexts, _, replay = replay_bootstrap
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            "CREATE FUNCTION public.bootstrap_replay_fixture_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture failure'; END $$"
        )
        admin.execute(
            "CREATE TRIGGER bootstrap_replay_fixture_failure BEFORE INSERT ON public.outbox_events FOR EACH ROW EXECUTE FUNCTION public.bootstrap_replay_fixture_failure()"
        )
    with pytest.raises(AppError):
        replay.create_project_with_key(
            contexts[0].user_id, ProjectCreate(name="failed replay"), uuid4(), "rollback-op"
        )
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        assert (
            admin.execute(
                "SELECT count(*) FROM paperpilot_business.bootstrap_requests WHERE operation_key='rollback-op'"
            ).fetchone()[0]
            == 0
        )
        assert (
            admin.execute("SELECT count(*) FROM public.projects WHERE name='failed replay'").fetchone()[0]
            == 0
        )
        admin.execute("DROP TRIGGER bootstrap_replay_fixture_failure ON public.outbox_events")
    recovered = replay.create_project_with_key(
        contexts[0].user_id, ProjectCreate(name="failed replay"), uuid4(), "rollback-op"
    )
    assert recovered.owner_id == contexts[0].user_id
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT UPDATE (name) ON paperpilot_business.bootstrap_requests TO {}").format(
                sql.Identifier(OWNER)
            )
        )
    with pytest.raises(AppError):
        replay.ready()


def test_project_replay_after_simulated_commit_ack_loss(replay_bootstrap, monkeypatch):
    config, contexts, _, replay = replay_bootstrap
    acquire = replay.database._acquire

    class LostAcknowledgement:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def commit(self):
            self.connection.commit()  # Real isolated PostgreSQL commit succeeds.
            raise psycopg.OperationalError("simulated lost acknowledgement")

    monkeypatch.setattr(replay.database, "_acquire", lambda: LostAcknowledgement(acquire()))
    data = ProjectCreate(name="lost acknowledgement")
    with pytest.raises(AppError) as unknown:
        replay.create_project_with_key(contexts[0].user_id, data, uuid4(), "lost-ack")
    assert unknown.value.code == "DATA_COMMIT_UNKNOWN"
    restarted = ScopedReplayProjectCreator(replay.config)
    try:
        project = restarted.create_project_with_key(contexts[0].user_id, data, uuid4(), "lost-ack")
    finally:
        restarted.close()
    with psycopg.connect(**config.conninfo()) as admin:
        assert (
            admin.execute(
                "SELECT count(*) FROM public.projects WHERE name='lost acknowledgement'"
            ).fetchone()[0]
            == 1
        )
        assert (
            admin.execute(
                "SELECT count(*) FROM public.outbox_events WHERE project_id=%s", (project.project_id,)
            ).fetchone()[0]
            == 1
        )
