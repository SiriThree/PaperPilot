"""Explicit administrator operations, limited to logins registered by this tool."""

from importlib.resources import files
import re
from uuid import UUID


def _name(role):
    if not isinstance(role, str) or re.fullmatch(r"pp_(reader|audit|business)_[a-z0-9_]{1,48}", role) is None:
        raise ValueError("Use a pp_reader_, pp_audit_ or pp_business_ managed login name")


def _idle_admin(connection):
    from psycopg.pq import TransactionStatus

    if not connection.autocommit or connection.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError("Use an idle autocommit administrator connection")


def install(connection, *, existing_views=False):
    from psycopg import sql

    _idle_admin(connection)
    with connection.transaction():
        if not existing_views:
            connection.execute(
                files("app.security")
                .joinpath("migrations/001_statistics_views.sql")
                .read_text(encoding="utf-8")
            )
        elif (
            connection.execute(
                "SELECT count(*) FROM pg_catalog.pg_views WHERE schemaname='paperpilot_security' "
                "AND viewname IN ('paper_statistics','paper_topics')"
            ).fetchone()[0]
            != 2
        ):
            raise ValueError("Install the approved statistics views first")
        connection.execute(
            files("app.security").joinpath("migrations/002_service_logins.sql").read_text(encoding="utf-8")
        )
        upgrade_business_registry(connection)
        # These are database-wide PUBLIC grants; only this explicit install command changes them.
        database = connection.execute("SELECT current_database()").fetchone()[0]
        connection.execute(
            sql.SQL("REVOKE CREATE,TEMPORARY ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database))
        )
        connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")


def _verifier(connection, role, password):
    if not isinstance(password, str) or not password or "\x00" in password:
        raise ValueError("A nonempty password must be supplied through the secret environment")
    return connection.pgconn.encrypt_password(
        password.encode("utf-8"), role.encode("utf-8"), b"scram-sha-256"
    ).decode("ascii")


def upgrade_business_registry(connection):
    """Explicit upgrade for installations that already have the 002 registry."""
    if not connection.autocommit:
        raise ValueError("Use an administrator autocommit connection")
    with connection.transaction():
        connection.execute(
            files("app.security").joinpath("migrations/003_business_login.sql").read_text(encoding="utf-8")
        )


BUSINESS_READ_TABLES = "users,projects,project_members,papers,project_papers,topics,paper_topics,chunks,embedding_models,chunk_embeddings,process_indexes,tasks,task_papers,task_attempts,task_results,result_sources,evidence_fields,reports,report_versions,report_version_sources,audit_events,outbox_events,idempotency_records,schema_migrations"
BUSINESS_INSERT_TABLES = "projects,project_members,papers,project_papers,chunks,embedding_models,chunk_embeddings,process_indexes,tasks,task_papers,task_attempts,task_results,result_sources,evidence_fields,reports,report_versions,report_version_sources,audit_events,outbox_events,idempotency_records"


def create_login(connection, role, password, *, project_id=None, business=False):
    from psycopg import sql

    _name(role)
    _idle_admin(connection)
    kind = "business" if business else "reader" if project_id is not None else "audit"
    if business and project_id is not None:
        raise ValueError("Business login cannot have a reader binding")
    if not role.startswith(f"pp_{kind}_") or (project_id is not None and type(project_id) is not UUID):
        raise ValueError("Login kind and project binding do not match")
    with connection.transaction():
        verifier = _verifier(connection, role, password)
        # CREATE ROLE refuses all existing names; no adoption or password overwrite.
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}"
            ).format(sql.Identifier(role), sql.Literal(verifier))
        )
        oid = connection.execute("SELECT oid FROM pg_catalog.pg_roles WHERE rolname=%s", (role,)).fetchone()[
            0
        ]
        connection.execute(
            "INSERT INTO paperpilot_security.service_logins(login_role,role_oid,kind,project_id) VALUES(%s,%s,%s,%s)",
            (role, oid, kind, project_id),
        )
        if kind == "reader":
            connection.execute(
                "INSERT INTO paperpilot_security.project_readers VALUES(%s,%s)", (role, project_id)
            )
            grants = [
                "GRANT USAGE ON SCHEMA paperpilot_security TO {}",
                "GRANT SELECT ON paperpilot_security.paper_statistics,paperpilot_security.paper_topics TO {}",
            ]
        elif kind == "audit":
            grants = [
                "GRANT USAGE ON SCHEMA public TO {}",
                "GRANT INSERT ON public.audit_events TO {}",
                "GRANT SELECT(event_id,event_fingerprint) ON public.audit_events TO {}",
                "GRANT SELECT(user_id) ON public.users TO {}",
                "GRANT SELECT(project_id) ON public.projects TO {}",
            ]
        else:
            grants = [
                "GRANT USAGE ON SCHEMA public TO {}",
                "GRANT SELECT ON "
                + ",".join("public." + t for t in BUSINESS_READ_TABLES.split(","))
                + " TO {}",
                "GRANT INSERT ON "
                + ",".join("public." + t for t in BUSINESS_INSERT_TABLES.split(","))
                + " TO {}",
                "GRANT UPDATE ON public.project_papers,public.tasks,public.task_attempts,public.reports,public.outbox_events TO {}",
                "GRANT DELETE ON public.project_members TO {}",
            ]
        for statement in grants:
            connection.execute(sql.SQL(statement).format(sql.Identifier(role)))


def _managed(connection, role):
    _name(role)
    row = connection.execute(
        "SELECT s.role_oid,s.kind,s.revoked_at FROM paperpilot_security.service_logins s "
        "JOIN pg_catalog.pg_roles r ON r.oid=s.role_oid AND r.rolname=s.login_role "
        "WHERE s.login_role=%s AND r.rolname<>session_user FOR UPDATE OF s",
        (role,),
    ).fetchone()
    if row is None:
        raise ValueError("Login is not owned by this lifecycle registry")
    return row


def _terminate(connection, oid):
    try:
        rows = connection.execute(
            "SELECT pg_terminate_backend(pid,1000) FROM pg_catalog.pg_stat_activity "
            "WHERE usesysid=%s AND pid<>pg_backend_pid()",
            (oid,),
        ).fetchall()
        if any(not row[0] for row in rows):
            raise RuntimeError()
    except Exception:
        raise RuntimeError("Credential change committed; session cleanup was not confirmed") from None


def rotate_login(connection, role, password):
    from psycopg import sql

    _idle_admin(connection)
    with connection.transaction():
        oid, _, revoked = _managed(connection, role)
        if revoked is not None:
            raise ValueError("Revoked logins cannot be reactivated by rotation")
        verifier = _verifier(connection, role, password)
        connection.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(verifier))
        )
    _terminate(connection, oid)


def revoke_login(connection, role):
    from psycopg import sql

    _idle_admin(connection)
    with connection.transaction():
        oid, kind, _ = _managed(connection, role)
        connection.execute(sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL").format(sql.Identifier(role)))
        if kind == "reader":
            connection.execute("DELETE FROM paperpilot_security.project_readers WHERE login_role=%s", (role,))
            statements = [
                "REVOKE ALL ON paperpilot_security.paper_statistics,paperpilot_security.paper_topics FROM {}",
                "REVOKE ALL ON SCHEMA paperpilot_security FROM {}",
            ]
        elif kind == "audit":
            statements = [
                "REVOKE INSERT ON public.audit_events FROM {}",
                "REVOKE SELECT(event_id,event_fingerprint) ON public.audit_events FROM {}",
                "REVOKE SELECT(user_id) ON public.users FROM {}",
                "REVOKE SELECT(project_id) ON public.projects FROM {}",
            ]
        else:
            statements = [
                "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}",
                "REVOKE ALL ON SCHEMA public FROM {}",
            ]
        for statement in statements:
            connection.execute(sql.SQL(statement).format(sql.Identifier(role)))
        connection.execute(
            "UPDATE paperpilot_security.service_logins SET revoked_at=COALESCE(revoked_at,clock_timestamp()) WHERE login_role=%s",
            (role,),
        )
    _terminate(connection, oid)
