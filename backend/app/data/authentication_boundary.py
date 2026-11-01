from contextlib import contextmanager
from pathlib import Path
import re

from app.data.database import ConnectionConfig
from app.errors import AppError
from app.secret_config import secret_value


def auth_config_from_env(environ, business):
    """Read candidate credentials from trusted process configuration, for localhost tests only."""
    keys = ("HOST", "PORT", "DBNAME", "USER", "PASSWORD")
    values = [
        secret_value(environ, "PAPERPILOT_AUTH_DB_PASSWORD")
        if key == "PASSWORD"
        else environ.get("PAPERPILOT_AUTH_DB_" + key, "")
        for key in keys
    ]
    if environ.get("PAPERPILOT_AUTH_DB_ISOLATED_TEST") != "1" or not all(values):
        raise RuntimeError("Explicit isolated authentication database configuration required")
    try:
        host, port, dbname, user, password = values
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("non-local database")
        candidate = ConnectionConfig(host, int(port), user, password, dbname)
        if not 1 <= candidate.port <= 65535:
            raise ValueError("invalid port")
        if (host, candidate.port, dbname) != (business.host, business.port, business.dbname):
            raise ValueError("different database")
        if user == business.user:
            raise ValueError("shared identity")
        return candidate
    except (ValueError, TypeError):
        raise RuntimeError("Invalid isolated authentication database configuration") from None


class AuthenticationBoundary:
    """Isolated candidate: validate both identities and the fixed credential lock."""

    def __init__(self, business, authentication, *, trusted_lock_owner):
        self.business = business
        self.authentication = authentication
        if not isinstance(trusted_lock_owner, str) or not trusted_lock_owner.strip():
            raise RuntimeError("Independently trusted authentication lock owner required")
        self.trusted_lock_owner = trusted_lock_owner
        if (business.config.host, business.config.port, business.config.dbname) != (
            authentication.config.host,
            authentication.config.port,
            authentication.config.dbname,
        ) or business.config.user == authentication.config.user:
            raise RuntimeError("Authentication and business identities must be distinct")

    def ready(self):
        try:
            with self.business.transaction() as connection:
                row = connection.execute(
                    "SELECT current_user=session_user AND current_user=%s AS identity, "
                    "NOT has_column_privilege(current_user,'public.users','password_hash','SELECT,UPDATE,INSERT,REFERENCES') AND NOT has_column_privilege(current_user,'public.users','disabled','UPDATE') AS no_hash, "
                    "NOT has_table_privilege(current_user,'public.auth_sessions','SELECT,INSERT,UPDATE,DELETE') AND NOT has_any_column_privilege(current_user,'public.auth_sessions','SELECT,INSERT,UPDATE,REFERENCES') AS no_sessions, "
                    "NOT has_table_privilege(current_user,'public.auth_login_limits','SELECT,INSERT,UPDATE,DELETE') AND NOT has_any_column_privilege(current_user,'public.auth_login_limits','SELECT,INSERT,UPDATE,REFERENCES') AS no_limits",
                    (self.business.config.user,),
                ).fetchone()
                if not row or not all(row.values()):
                    raise PermissionError("Business credential privileges invalid")
            with self.authentication.transaction() as connection:
                if connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND CASE WHEN c.relkind='S' THEN has_sequence_privilege(c.oid,'USAGE,SELECT,UPDATE') ELSE false END) AS unsafe"
                ).fetchone()["unsafe"]:
                    raise PermissionError("Authentication sequence privileges invalid")
                row = connection.execute(
                    "SELECT current_user=session_user AND current_user=%s AS identity, "
                    "rolcanlogin AND NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolinherit) AS role_ok, "
                    "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid) AS no_membership, "
                    "NOT has_database_privilege(current_user,current_database(),'CREATE,TEMP') AS database_ok, "
                    "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' "
                    "AND n.nspname<>'information_schema' AND has_schema_privilege(current_user,n.oid,'CREATE')) AS schemas_ok "
                    "FROM pg_roles r WHERE rolname=current_user",
                    (self.authentication.config.user,),
                ).fetchone()
                if not row or not all(row.values()):
                    raise PermissionError("Authentication identity invalid")
                row = connection.execute(
                    "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
                    "AND c.relkind IN ('r','p','v','m','f') AND c.oid NOT IN "
                    "(to_regclass('public.users'),to_regclass('public.auth_sessions'), "
                    "to_regclass('public.auth_login_limits'),to_regclass('public.audit_events'), "
                    "to_regclass('public.schema_migrations')) AND "
                    "(has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                    "OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS no_business, "
                    "NOT has_table_privilege(current_user,'public.audit_events','SELECT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AND NOT has_any_column_privilege(current_user,'public.audit_events','SELECT,UPDATE,REFERENCES') AND has_table_privilege(current_user,'public.audit_events','INSERT') AS audit_ok, "
                    "NOT has_table_privilege(current_user,'public.users','INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER') AND NOT has_any_column_privilege(current_user,'public.users','INSERT,REFERENCES') AND has_column_privilege(current_user,'public.users','password_hash','SELECT') AND NOT has_any_column_privilege(current_user,'public.users','UPDATE') AS users_ok, "
                    "NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid='public.users'::regclass "
                    "AND a.attnum>0 AND NOT a.attisdropped "
                    "AND has_column_privilege(current_user,a.attrelid,a.attnum,'UPDATE')) AS columns_ok, "
                    "has_table_privilege(current_user,'public.auth_sessions','SELECT') AND has_table_privilege(current_user,'public.auth_sessions','INSERT') AND has_table_privilege(current_user,'public.auth_sessions','UPDATE') "
                    "AND NOT has_table_privilege(current_user,'public.auth_sessions','DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                    "AND has_table_privilege(current_user,'public.auth_login_limits','SELECT') AND has_table_privilege(current_user,'public.auth_login_limits','INSERT') AND has_table_privilege(current_user,'public.auth_login_limits','UPDATE') "
                    "AND NOT has_table_privilege(current_user,'public.auth_login_limits','DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                    "AND NOT has_any_column_privilege(current_user,'public.auth_sessions','REFERENCES') "
                    "AND NOT has_any_column_privilege(current_user,'public.auth_login_limits','REFERENCES') AS sessions_ok, "
                    "NOT has_table_privilege(current_user,'public.schema_migrations','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                    "AND NOT has_any_column_privilege(current_user,'public.schema_migrations','SELECT,INSERT,UPDATE,REFERENCES') AS migrations_ok, "
                    "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND p.prosecdef "
                    "AND p.oid<>COALESCE(to_regprocedure('paperpilot_auth.lock_credential(uuid)')::oid,0::oid) "
                    "AND has_function_privilege(current_user,p.oid,'EXECUTE')) AS no_definer, "
                    "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='pg_catalog' "
                    "AND p.proname=ANY(ARRAY['pg_read_file','pg_read_binary_file','pg_ls_dir','pg_stat_file']) "
                    "AND has_function_privilege(current_user,p.oid,'EXECUTE')) AS no_server_files, "
                    "NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid='public.users'::regclass AND a.attnum>0 "
                    "AND NOT a.attisdropped AND a.attname NOT IN ('user_id','username','password_hash','disabled') "
                    "AND has_column_privilege(current_user,a.attrelid,a.attnum,'SELECT')) AS no_extra_user_columns"
                ).fetchone()
                if not row or not all(row.values()):
                    raise PermissionError("Authentication privileges invalid")
                source = (
                    Path(__file__).parents[1] / "security/migrations/004_auth_lock_candidate.sql"
                ).read_text(encoding="utf-8-sig")
                match = re.search(r"AS \$\$(.*?)\$\$;", source, flags=re.S)
                if not match:
                    raise PermissionError("Authentication lock baseline unavailable")
                function = connection.execute(
                    "SELECT pg_get_userbyid(p.proowner) AS owner, p.prosecdef AS definer, "
                    "p.provolatile='v' AS volatile, p.proconfig=ARRAY['search_path=pg_catalog'] AS safe_path, "
                    "p.prosrc AS body, has_function_privilege(current_user,p.oid,'EXECUTE') AS allowed, "
                    "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
                    "WHERE a.privilege_type='EXECUTE' AND (a.grantee NOT IN (p.proowner,(SELECT oid FROM pg_roles WHERE rolname=current_user)) OR a.is_grantable)) AS no_public, "
                    "pg_get_function_result(p.oid)='TABLE(password_hash text, disabled boolean)' AS result_ok "
                    "FROM pg_proc p WHERE p.oid=to_regprocedure('paperpilot_auth.lock_credential(uuid)') "
                    "AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')"
                ).fetchone()
                if (
                    not function
                    or function["owner"] != self.trusted_lock_owner
                    or function["body"] != match.group(1)
                    or not all(
                        function[key]
                        for key in ("definer", "volatile", "safe_path", "allowed", "no_public", "result_ok")
                    )
                ):
                    raise PermissionError("Authentication lock function invalid")
                owner = connection.execute(
                    "SELECT NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolcanlogin OR rolinherit) AS role_ok, "
                    "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) AS no_membership, "
                    "NOT has_database_privilege(r.oid,current_database(),'CREATE,TEMP') AS db_ok, "
                    "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND has_schema_privilege(r.oid,n.oid,'CREATE')) AS schemas_ok, "
                    "has_column_privilege(r.oid,'public.users','user_id','SELECT') AND has_column_privilege(r.oid,'public.users','disabled','SELECT') AND has_column_privilege(r.oid,'public.users','password_hash','SELECT') AND has_column_privilege(r.oid,'public.users','password_hash','UPDATE') AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid='public.users'::regclass AND a.attnum>0 AND NOT a.attisdropped AND a.attname NOT IN ('user_id','disabled','password_hash') AND has_column_privilege(r.oid,a.attrelid,a.attnum,'SELECT')) AS lock_ok, "
                    "NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f') AND c.oid<>'public.users'::regclass AND (has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR has_any_column_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS no_other_tables, "
                    "NOT has_table_privilege(r.oid,'public.users','INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER') AND NOT has_any_column_privilege(r.oid,'public.users','INSERT,REFERENCES') AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid='public.users'::regclass AND a.attnum>0 AND NOT a.attisdropped AND a.attname<>'password_hash' AND has_column_privilege(r.oid,a.attrelid,a.attnum,'UPDATE')) AS users_ok, "
                    "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND p.prosecdef AND p.oid<>to_regprocedure('paperpilot_auth.lock_credential(uuid)') AND has_function_privilege(r.oid,p.oid,'EXECUTE')) "
                    "AND NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='pg_catalog' AND p.proname=ANY(ARRAY['pg_read_file','pg_read_binary_file','pg_ls_dir','pg_stat_file']) AND has_function_privilege(r.oid,p.oid,'EXECUTE')) AS functions_ok "
                    "FROM pg_roles r WHERE r.rolname=%s",
                    (self.trusted_lock_owner,),
                ).fetchone()
                if not owner or not all(owner.values()):
                    raise PermissionError("Authentication lock owner invalid")
            return True
        except Exception:
            raise AppError(503, "AUTH_UNAVAILABLE", "认证数据库安全配置不可用") from None

    @staticmethod
    def lock_credential(connection, user_id):
        return connection.execute("SELECT * FROM paperpilot_auth.lock_credential(%s)", (user_id,)).fetchone()

    @contextmanager
    def transaction(self, write=False):
        self.ready()
        with self.authentication.transaction(write=write) as connection:
            yield connection
