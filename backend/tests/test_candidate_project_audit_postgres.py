"""Opt-in isolated candidate: dedicated login identity scopes audit reads in PostgreSQL.

This does not test production provisioning or integrate the candidate with DataPort.
"""

import secrets
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from app.data.binding import compose_data_binding
from app.data.settings import DataSettings
from app.schemas import ProjectCreate
from test_phase5a_persistent import database as isolated_database  # noqa: F401

pytestmark = pytest.mark.real_database


def test_candidate_audit_isolation_and_binding_revocation(isolated_database):  # noqa: F811
    config, _ = isolated_database
    binding = compose_data_binding(config, DataSettings())
    try:
        alice = binding.data.user_by_name("alice")
        bob = binding.data.user_by_name("bob")
        first = binding.data.create_project(alice.user_id, ProjectCreate(name="audit A"), uuid4())
        second = binding.data.create_project(bob.user_id, ProjectCreate(name="audit B"), uuid4())
    finally:
        binding.close()

    roles = ["audit_candidate_" + uuid4().hex[:12] for _ in range(3)]
    owner = "audit_candidate_owner_" + uuid4().hex[:12]
    passwords = [secrets.token_urlsafe(32) for _ in roles]
    events = [uuid4(), uuid4()]
    migration = (
        Path(__file__).resolve().parents[1] / "app/security/migrations/003_candidate_scoped_audit.sql"
    ).read_text(encoding="utf-8")

    # The administrator only prepares an isolated disposable database. Reader
    # assertions below use new, unprivileged database logins.
    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        admin.execute(migration, prepare=False)
        admin.execute(
            sql.SQL("REVOKE CREATE, TEMPORARY ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(config.dbname)
            )
        )
        admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT "
                "NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public, paperpilot_audit TO {}").format(sql.Identifier(owner))
        )
        admin.execute(sql.SQL("GRANT CREATE ON SCHEMA paperpilot_audit TO {}").format(sql.Identifier(owner)))
        admin.execute(
            sql.SQL(
                "GRANT SELECT (event_id,timestamp,request_id,actor_id,project_id,action,resource_type,"
                "decision,execution_status,policy_id,duration_ms,mock,details) ON public.audit_events TO {}"
            ).format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("GRANT SELECT (project_id,user_id,role) ON public.project_members TO {}").format(
                sql.Identifier(owner)
            )
        )
        admin.execute(
            sql.SQL("GRANT SELECT (user_id,disabled) ON public.users TO {}").format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("GRANT SELECT ON paperpilot_audit.candidate_readers TO {}").format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("ALTER VIEW paperpilot_audit.candidate_project_events OWNER TO {}").format(
                sql.Identifier(owner)
            )
        )
        admin.execute(
            sql.SQL(
                "ALTER FUNCTION paperpilot_audit.candidate_binding_matches(uuid,uuid) OWNER TO {}"
            ).format(sql.Identifier(owner))
        )
        admin.execute(
            sql.SQL("REVOKE CREATE ON SCHEMA paperpilot_audit FROM {}").format(sql.Identifier(owner))
        )
        for role, password in zip(roles, passwords, strict=True):
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT "
                    "NOCREATEDB NOCREATEROLE NOREPLICATION"
                ).format(sql.Identifier(role), sql.Literal(password))
            )
            admin.execute(
                sql.SQL("GRANT USAGE ON SCHEMA paperpilot_audit TO {}").format(sql.Identifier(role))
            )
            admin.execute(
                sql.SQL("GRANT SELECT ON paperpilot_audit.candidate_project_events TO {}").format(
                    sql.Identifier(role)
                )
            )
            admin.execute(
                sql.SQL(
                    "GRANT EXECUTE ON FUNCTION paperpilot_audit.candidate_binding_matches(uuid,uuid) TO {}"
                ).format(sql.Identifier(role))
            )
        for role, project, user in [
            (roles[0], first.project_id, alice.user_id),
            (roles[1], second.project_id, bob.user_id),
        ]:
            admin.execute(
                "INSERT INTO paperpilot_audit.candidate_readers VALUES (%s,%s,%s)", (role, project, user)
            )
        for event, project in zip(events, [first.project_id, second.project_id], strict=True):
            admin.execute(
                "INSERT INTO public.audit_events "
                "(event_id,timestamp,request_id,project_id,action,resource_type,decision,"
                "execution_status,duration_ms,mock,event_fingerprint) "
                "VALUES (%s,now(),%s,%s,'candidate.fixture','fixture','allowed','completed',0,false,%s)",
                (event, uuid4(), project, "a" * 64),
            )

    def reader(index):
        return psycopg.connect(**{**config.conninfo(), "user": roles[index], "password": passwords[index]})

    def valid(conn, user, project):
        return conn.execute(
            "SELECT paperpilot_audit.candidate_binding_matches(%s,%s)", (user, project)
        ).fetchone()[0]

    def visible(conn):
        return conn.execute("SELECT event_id FROM paperpilot_audit.candidate_project_events").fetchall()

    with reader(0) as a, reader(1) as b, reader(2) as missing:
        role_attributes = a.execute(
            "SELECT rolcanlogin,rolsuper,rolbypassrls,rolinherit,rolcreatedb,rolcreaterole,rolreplication,"
            "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid) "
            "FROM pg_roles r WHERE rolname=session_user"
        ).fetchone()
        assert role_attributes == (True, False, False, False, False, False, False, True)
        assert valid(a, alice.user_id, first.project_id) and visible(a) == [(events[0],)]
        assert valid(b, bob.user_id, second.project_id) and visible(b) == [(events[1],)]
        assert not valid(a, alice.user_id, second.project_id)
        assert not valid(a, bob.user_id, first.project_id)
        assert not valid(missing, alice.user_id, first.project_id) and visible(missing) == []
        assert (
            a.execute(
                "SELECT event_id FROM paperpilot_audit.candidate_project_events WHERE project_id=%s",
                (second.project_id,),
            ).fetchall()
            == []
        )
        # An arbitrary caller-provided project setting cannot change SESSION_USER.
        a.execute("SELECT set_config('paperpilot.project_id',%s,true)", (str(second.project_id),))
        assert visible(a) == [(events[0],)]
        for statement in [
            "SELECT * FROM public.audit_events",
            "SELECT * FROM paperpilot_audit.candidate_readers",
            "SELECT password_hash FROM public.users",
        ]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                a.execute(statement)
            a.rollback()
        for target in [roles[1], owner]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                a.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(target)))
            a.rollback()

    with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
        with pytest.raises(psycopg.errors.UniqueViolation):
            admin.execute(
                "INSERT INTO paperpilot_audit.candidate_readers VALUES (%s,%s,%s)",
                (roles[0], second.project_id, bob.user_id),
            )
        admin.execute(
            "UPDATE paperpilot_audit.candidate_readers SET user_id=%s WHERE login_role=%s",
            (bob.user_id, roles[0]),
        )
        with reader(0) as a:
            assert not valid(a, bob.user_id, first.project_id) and visible(a) == []
        admin.execute(
            "UPDATE paperpilot_audit.candidate_readers SET user_id=%s WHERE login_role=%s",
            (alice.user_id, roles[0]),
        )
        admin.execute("UPDATE public.users SET disabled=true WHERE user_id=%s", (alice.user_id,))
        with reader(0) as a:
            assert not valid(a, alice.user_id, first.project_id) and visible(a) == []
        admin.execute("UPDATE public.users SET disabled=false WHERE user_id=%s", (alice.user_id,))
        with reader(0) as a:
            assert valid(a, alice.user_id, first.project_id)
            admin.execute("DELETE FROM paperpilot_audit.candidate_readers WHERE login_role=%s", (roles[0],))
            # READ COMMITTED gets a new snapshot on the next statement even on
            # an already-open connection; long-lived repeatable-read snapshots
            # are deliberately outside this candidate's guarantee.
            assert not valid(a, alice.user_id, first.project_id) and visible(a) == []

    # Only owners may read audit; a bound member remains denied both before and
    # after membership removal. The current schema makes project owners immutable.
    with psycopg.connect(**config.conninfo()) as admin:
        admin.execute(
            "INSERT INTO paperpilot_audit.candidate_readers VALUES (%s,%s,%s)",
            (roles[0], first.project_id, bob.user_id),
        )
        admin.execute(
            "INSERT INTO public.project_members(project_id,user_id,role) VALUES (%s,%s,'member')",
            (first.project_id, bob.user_id),
        )
    with reader(0) as a:
        assert not valid(a, bob.user_id, first.project_id) and visible(a) == []
    with psycopg.connect(**config.conninfo()) as admin:
        admin.execute(
            "DELETE FROM public.project_members WHERE project_id=%s AND user_id=%s",
            (first.project_id, bob.user_id),
        )
    with reader(0) as a:
        assert not valid(a, bob.user_id, first.project_id) and visible(a) == []
