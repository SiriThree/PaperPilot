"""Dedicated least-privilege identity, retaining A's append_audit transaction and idempotency."""

from collections.abc import Callable
from dataclasses import dataclass

from app.schemas import AuditEvent
from app.security.executor import QueryUnavailable, verify_login

_FINGERPRINT_BODY = (
    "\n    SELECT event_fingerprint FROM public.audit_events WHERE event_id = requested_event\n"
)


def verify_writer(connection, user):
    from psycopg.rows import tuple_row

    with connection.cursor(row_factory=tuple_row) as cursor:
        verify_login(cursor, user)
        unsafe = cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND ("
            " (c.relkind IN ('r','p','v','m','f') AND ("
            "   has_table_privilege(c.oid,'SELECT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            "   OR has_table_privilege(c.oid,'INSERT WITH GRANT OPTION') "
            "   OR has_any_column_privilege(c.oid,'UPDATE,REFERENCES') "
            "   OR ((has_table_privilege(c.oid,'INSERT') OR has_any_column_privilege(c.oid,'INSERT')) "
            "       AND NOT (n.nspname='public' AND c.relname='audit_events')))) "
            " OR (c.relkind='S' AND has_sequence_privilege(c.oid,'USAGE,SELECT,UPDATE')))) "
            "OR EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a "
            "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE a.attnum>0 AND NOT a.attisdropped AND c.relkind IN ('r','p','v','m','f') "
            "AND n.nspname !~ '^pg_' AND n.nspname<>'information_schema' "
            "AND (has_column_privilege(c.oid,a.attnum,'SELECT WITH GRANT OPTION') OR "
            "(has_column_privilege(c.oid,a.attnum,'SELECT') AND NOT (n.nspname='public' AND ("
            " (c.relname='users' AND a.attname='user_id') "
            " OR (c.relname='projects' AND a.attname='project_id')))))) "
            "OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' "
            "AND p.prosecdef AND has_function_privilege(p.oid,'EXECUTE') "
            "AND p.oid<>COALESCE(to_regprocedure('paperpilot_audit.event_fingerprint(uuid)')::oid,0::oid))"
        ).fetchone()[0]
        required = cursor.execute(
            "SELECT has_table_privilege('public.audit_events','INSERT') "
            "AND has_column_privilege('public.users','user_id','SELECT') "
            "AND has_column_privilege('public.projects','project_id','SELECT')"
        ).fetchone()[0]
        if unsafe or not required:
            raise QueryUnavailable()
        # Only A's reviewed idempotency lookup may cross this SECURITY DEFINER boundary.
        function = cursor.execute(
            "SELECT p.prosecdef,p.provolatile='s',p.proconfig=ARRAY['search_path=pg_catalog'], "
            "p.prosrc=%s,p.proowner<>(SELECT oid FROM pg_roles WHERE rolname=current_user), "
            "p.prorettype='character varying'::regtype,p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql'), "
            "has_schema_privilege('paperpilot_audit','USAGE'),has_function_privilege(p.oid,'EXECUTE'), "
            "NOT has_function_privilege(p.oid,'EXECUTE WITH GRANT OPTION'), "
            "NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
            "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') "
            "FROM pg_proc p WHERE p.oid=to_regprocedure('paperpilot_audit.event_fingerprint(uuid)')",
            (_FINGERPRINT_BODY,),
        ).fetchone()
        if not function or not all(function):
            raise QueryUnavailable()


@dataclass(frozen=True)
class AuditBinding:
    append: Callable[[AuditEvent], None]
    close: Callable[[], None]


def compose_audit(config) -> AuditBinding:
    # A stays an explicit composition dependency, never copied into D or replaced by Mock.
    from app.data.adapter import PostgresData
    from app.data.database import PostgresDatabase
    from app.data.settings import DataSettings

    database = PostgresDatabase(config, pool_size=1)

    def before_commit(operation, connection):
        if operation != "append_audit":
            raise QueryUnavailable()
        verify_writer(connection, config.user)

    try:
        with database.transaction() as connection:
            verify_writer(connection, config.user)
        database.set_transaction_guard(lambda connection: verify_writer(connection, config.user))
        data = PostgresData(database, DataSettings(), before_commit=before_commit)
        return AuditBinding(append=data.append_audit, close=database.close)
    except Exception:
        database.close()
        raise RuntimeError("Restricted audit identity could not be verified") from None
