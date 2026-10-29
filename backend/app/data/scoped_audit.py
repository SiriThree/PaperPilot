"""Per-identity scoped audit readers for explicitly isolated tests only.

The login-to-user/project assignment comes from trusted server configuration,
never HTTP input. Each request creates and closes its own connection.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row, tuple_row

from app.errors import AppError
from app.schemas import AuditEvent, AuditList
from app.secret_config import secret_value

# PostgreSQL 16 pg_get_viewdef(..., false) with search_path fixed to pg_catalog,
# derived from the version-controlled candidate migration, not deployment state.
VIEW_SHA256 = "6314adf0b595906a71c7aaa6bdc9a8a68b41f6704a1465bc34fee12a86af00d6"
_SOURCE = Path(__file__).parents[1] / "security/migrations/003_candidate_scoped_audit.sql"


@dataclass(frozen=True)
class ScopedAuditConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False)
    user_id: UUID = field(default=None)
    project_id: UUID = field(default=None)
    owner: str = ""
    binding_owner: str = ""

    def validate(self):
        if (
            self.host not in {"127.0.0.1", "localhost", "::1"}
            or not 1 <= self.port <= 65535
            or not all((self.dbname, self.user, self.password, self.owner, self.binding_owner))
            or not isinstance(self.user_id, UUID)
            or not isinstance(self.project_id, UUID)
        ):
            raise ValueError("Invalid isolated scoped audit configuration")


def verify_scoped_audit(connection, config: ScopedAuditConfig):
    """Validate checked-in view/function and role grants; fail closed on uncertainty."""
    config.validate()
    if connection.info.server_version // 10000 != 16:
        raise PermissionError("Unsupported scoped audit baseline")
    source = _SOURCE.read_text(encoding="utf-8")
    matched = re.search(r"AS \$\$(.*?)\$\$;", source, flags=re.S)
    if not matched:
        raise PermissionError("Scoped audit baseline unavailable")
    with connection.cursor(row_factory=tuple_row) as cur:
        cur.execute("SET LOCAL search_path=pg_catalog")
        cur.execute(
            "SELECT current_user=session_user AND current_user=%s, "
            "rolcanlogin AND NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolinherit), "
            "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid), "
            "NOT has_database_privilege(current_user,current_database(),'CREATE,TEMP'), "
            "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' "
            "AND n.nspname<>'information_schema' AND has_schema_privilege(current_user,n.oid,'CREATE')) "
            "FROM pg_roles r WHERE rolname=current_user",
            (config.user,),
        )
        row = cur.fetchone()
        if not row or not all(row):
            raise PermissionError("Scoped audit reader role invalid")
        cur.execute(
            "SELECT NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolcanlogin OR rolinherit), "
            "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid), "
            "NOT has_database_privilege(r.oid,current_database(),'CREATE,TEMP'), "
            "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' "
            "AND n.nspname<>'information_schema' AND has_schema_privilege(r.oid,n.oid,'CREATE')) "
            "FROM pg_roles r WHERE rolname=%s",
            (config.owner,),
        )
        row = cur.fetchone()
        if not row or not all(row):
            raise PermissionError("Scoped audit owner invalid")
        cur.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
            "AND c.relkind IN ('r','p','v','m','f') "
            "AND c.oid<>COALESCE(to_regclass('paperpilot_audit.candidate_project_events')::oid,0::oid) "
            "AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            "OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))"
        )
        if not cur.fetchone()[0]:
            raise PermissionError("Scoped audit reader has forbidden relation privileges")
        cur.execute(
            "SELECT pg_get_userbyid(c.relowner), 'security_barrier=true'=ANY(COALESCE(c.reloptions,ARRAY[]::text[])), "
            "NOT ('security_invoker=true'=ANY(COALESCE(c.reloptions,ARRAY[]::text[]))), "
            "has_table_privilege(current_user,c.oid,'SELECT'), "
            "NOT has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'), "
            "NOT has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES'),pg_get_viewdef(c.oid,false) "
            "FROM pg_class c WHERE c.oid=to_regclass('paperpilot_audit.candidate_project_events') AND c.relkind='v'"
        )
        view = cur.fetchone()
        if (
            not view
            or view[0] != config.owner
            or not all(view[1:6])
            or hashlib.sha256(view[6].encode("utf-8")).hexdigest() != VIEW_SHA256
        ):
            raise PermissionError("Scoped audit view invalid")
        cur.execute(
            "SELECT pg_get_userbyid(p.proowner), p.prosecdef, p.provolatile='s', "
            "p.proconfig=ARRAY['search_path=pg_catalog'],p.prosrc, "
            "has_function_privilege(current_user,p.oid,'EXECUTE'), "
            "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
            "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') "
            "FROM pg_proc p WHERE p.oid=to_regprocedure('paperpilot_audit.candidate_binding_matches(uuid,uuid)') "
            "AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql') AND p.prorettype='boolean'::regtype"
        )
        function = cur.fetchone()
        if (
            not function
            or function[0] != config.owner
            or not all(function[1:4])
            or function[4] != matched.group(1)
            or not all(function[5:])
        ):
            raise PermissionError("Scoped audit binding function invalid")
        cur.execute(
            "SELECT pg_get_userbyid(c.relowner), c.relkind IN ('r','p'), "
            "EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='p' "
            "AND k.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='login_role' AND attnotnull)]::smallint[]), "
            "NOT has_table_privilege(%s,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'), "
            "NOT has_any_column_privilege(%s,c.oid,'INSERT,UPDATE,REFERENCES') "
            "FROM pg_class c WHERE c.oid=to_regclass('paperpilot_audit.candidate_readers')",
            (config.owner, config.owner),
        )
        binding = cur.fetchone()
        if not binding or binding[0] != config.binding_owner or not all(binding[1:]):
            raise PermissionError("Scoped audit binding table invalid")
        cur.execute(
            "SELECT count(*)=2 FROM pg_attribute a WHERE a.attrelid=to_regclass('paperpilot_audit.candidate_readers') "
            "AND a.attname=ANY(%s) AND a.attnotnull AND NOT a.attisdropped",
            (["user_id", "project_id"],),
        )
        if not cur.fetchone()[0]:
            raise PermissionError("Scoped audit binding columns invalid")
        cur.execute(
            "SELECT count(*)=2 FROM pg_constraint k WHERE k.conrelid=to_regclass('paperpilot_audit.candidate_readers') "
            "AND k.contype='f' AND k.convalidated AND ( "
            "(k.confrelid=to_regclass('public.projects') AND "
            "k.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=k.conrelid AND attname='project_id')]::smallint[] AND "
            "k.confkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=k.confrelid AND attname='project_id')]::smallint[]) OR "
            "(k.confrelid=to_regclass('public.users') AND "
            "k.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=k.conrelid AND attname='user_id')]::smallint[] AND "
            "k.confkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=k.confrelid AND attname='user_id')]::smallint[]))"
        )
        if not cur.fetchone()[0]:
            raise PermissionError("Scoped audit binding foreign keys invalid")
        cur.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_class c, "
            "LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a "
            "WHERE c.oid=to_regclass('paperpilot_audit.candidate_readers') AND a.grantee<>c.relowner "
            "AND a.privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'))"
        )
        if not cur.fetchone()[0]:
            raise PermissionError("Scoped audit binding table write grants invalid")
        cur.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid, "
            "LATERAL aclexplode(a.attacl) grant_entry "
            "WHERE c.oid=to_regclass('paperpilot_audit.candidate_readers') "
            "AND grant_entry.grantee<>c.relowner AND grant_entry.privilege_type IN ('INSERT','UPDATE','REFERENCES'))"
        )
        if not cur.fetchone()[0]:
            raise PermissionError("Scoped audit binding column grants invalid")
        cur.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
            "AND c.relkind IN ('r','p','v','m','f') "
            "AND c.oid NOT IN (to_regclass('public.audit_events'),to_regclass('public.project_members'), "
            "to_regclass('public.users'),to_regclass('paperpilot_audit.candidate_readers'), "
            "to_regclass('paperpilot_audit.candidate_project_events')) "
            "AND (has_table_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            "OR has_any_column_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) ",
            (config.owner, config.owner),
        )
        if not cur.fetchone()[0]:
            raise PermissionError("Scoped audit owner has forbidden relation privileges")
        cur.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
            "AND c.relkind IN ('r','p') AND c.oid IN (to_regclass('public.audit_events'), "
            "to_regclass('public.project_members'),to_regclass('public.users'),to_regclass('paperpilot_audit.candidate_readers')) "
            "AND (has_table_privilege(%s,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            "OR has_any_column_privilege(%s,c.oid,'INSERT,UPDATE,REFERENCES'))) ",
            (config.owner, config.owner),
        )
        if not cur.fetchone()[0]:
            raise PermissionError("Scoped audit owner has forbidden write privileges")
        cur.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
            "WHERE a.attnum>0 AND NOT a.attisdropped AND c.oid IN (to_regclass('public.audit_events'), "
            "to_regclass('public.project_members'),to_regclass('public.users'),to_regclass('paperpilot_audit.candidate_readers')) "
            "AND has_column_privilege(%s,c.oid,a.attnum,'SELECT') AND NOT ( "
            "(c.oid=to_regclass('public.audit_events') AND a.attname=ANY(%s)) OR "
            "(c.oid=to_regclass('public.project_members') AND a.attname=ANY(%s)) OR "
            "(c.oid=to_regclass('public.users') AND a.attname=ANY(%s)) OR "
            "(c.oid=to_regclass('paperpilot_audit.candidate_readers') AND a.attname=ANY(%s))))",
            (
                config.owner,
                [
                    "event_id",
                    "timestamp",
                    "request_id",
                    "actor_id",
                    "project_id",
                    "action",
                    "resource_type",
                    "decision",
                    "execution_status",
                    "policy_id",
                    "duration_ms",
                    "mock",
                ],
                ["project_id", "user_id", "role"],
                ["user_id", "disabled"],
                ["login_role", "project_id", "user_id"],
            ),
        )
        if not cur.fetchone()[0]:
            raise PermissionError("Scoped audit owner has forbidden column privileges")
        cur.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM (VALUES "
            "(to_regclass('public.audit_events'),%s::text[]), "
            "(to_regclass('public.project_members'),%s::text[]), "
            "(to_regclass('public.users'),%s::text[]), "
            "(to_regclass('paperpilot_audit.candidate_readers'),%s::text[])) required(obj,cols) "
            "CROSS JOIN LATERAL unnest(required.cols) column_name "
            "WHERE required.obj IS NULL OR NOT has_column_privilege(%s,required.obj,column_name,'SELECT'))",
            (
                [
                    "event_id",
                    "timestamp",
                    "request_id",
                    "actor_id",
                    "project_id",
                    "action",
                    "resource_type",
                    "decision",
                    "execution_status",
                    "policy_id",
                    "duration_ms",
                    "mock",
                ],
                ["project_id", "user_id", "role"],
                ["user_id", "disabled"],
                ["login_role", "project_id", "user_id"],
                config.owner,
            ),
        )
        if not cur.fetchone()[0]:
            raise PermissionError("Scoped audit owner missing required privileges")
        cur.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
            "AND CASE WHEN c.relkind='S' THEN "
            "(has_sequence_privilege(current_user,c.oid,'USAGE,SELECT,UPDATE') OR "
            "has_sequence_privilege(%s,c.oid,'USAGE,SELECT,UPDATE')) ELSE false END)",
            (config.owner,),
        )
        if not cur.fetchone()[0]:
            raise PermissionError("Scoped audit sequence privileges invalid")
        cur.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND p.prosecdef "
            "AND p.oid<>COALESCE(to_regprocedure('paperpilot_audit.candidate_binding_matches(uuid,uuid)')::oid,0::oid) "
            "AND (has_function_privilege(current_user,p.oid,'EXECUTE') OR has_function_privilege(%s,p.oid,'EXECUTE'))) ",
            (config.owner,),
        )
        if not cur.fetchone()[0]:
            raise PermissionError("Scoped audit unexpected definer function")
        cur.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='pg_catalog' AND p.proname=ANY(%s) "
            "AND (has_function_privilege(current_user,p.oid,'EXECUTE') OR has_function_privilege(%s,p.oid,'EXECUTE'))) ",
            (
                [
                    "pg_read_file",
                    "pg_read_binary_file",
                    "pg_ls_dir",
                    "pg_stat_file",
                    "pg_ls_logdir",
                    "pg_ls_waldir",
                    "pg_ls_archive_statusdir",
                ],
                config.owner,
            ),
        )
        if not cur.fetchone()[0]:
            raise PermissionError("Scoped audit server file privileges")
        cur.execute(
            "SELECT paperpilot_audit.candidate_binding_matches(%s,%s)", (config.user_id, config.project_id)
        )
        if not cur.fetchone()[0]:
            raise PermissionError("Scoped audit identity binding invalid")


class ScopedAuditReader:
    def __init__(self, config: ScopedAuditConfig):
        config.validate()
        self.config = config

    def _connect(self):
        c = self.config
        return psycopg.connect(
            host=c.host,
            port=c.port,
            dbname=c.dbname,
            user=c.user,
            password=c.password,
            connect_timeout=3,
            row_factory=dict_row,
        )

    def ready(self):
        try:
            with self._connect() as conn:
                verify_scoped_audit(conn, self.config)
            return True
        except (OSError, psycopg.Error, PermissionError, ValueError):
            raise AppError(503, "AUDIT_UNAVAILABLE", "审计安全配置不可用") from None

    def audit(self, ctx, *, action, decision, actor_id, since, until, offset, limit):
        if ctx.user_id != self.config.user_id or ctx.project_id != self.config.project_id:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        try:
            with self._connect() as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                verify_scoped_audit(conn, self.config)
                where = (
                    " FROM paperpilot_audit.candidate_project_events WHERE project_id=%s"
                    " AND (%s::text IS NULL OR action=%s) AND (%s::text IS NULL OR decision=%s)"
                    " AND (%s::uuid IS NULL OR actor_id=%s)"
                    " AND (%s::timestamptz IS NULL OR timestamp >= %s)"
                    " AND (%s::timestamptz IS NULL OR timestamp <= %s)"
                )
                args = (
                    ctx.project_id,
                    action,
                    action,
                    decision,
                    decision,
                    actor_id,
                    actor_id,
                    since,
                    since,
                    until,
                    until,
                )
                total = conn.execute("SELECT count(*) AS total" + where, args).fetchone()["total"]
                rows = conn.execute(
                    "SELECT *" + where + " ORDER BY timestamp DESC,event_id DESC LIMIT %s OFFSET %s",
                    (*args, limit, offset),
                ).fetchall()
            return AuditList(
                items=[AuditEvent(**row) for row in rows], total=total, offset=offset, limit=limit, mock=False
            )
        except (OSError, psycopg.Error, PermissionError, ValueError):
            raise AppError(503, "AUDIT_UNAVAILABLE", "审计安全配置不可用") from None


class ScopedAuditRouter:
    """Immutable server-side mapping; each underlying read uses its own connection."""

    def __init__(self, configurations):
        if not 1 <= len(configurations) <= 64:
            raise ValueError("Invalid scoped audit mapping count")
        readers = {}
        logins = set()
        baseline = None
        for config in configurations:
            config.validate()
            target = (config.host, config.port, config.dbname, config.owner, config.binding_owner)
            if baseline is not None and target != baseline:
                raise ValueError("Scoped audit mappings must share a trusted database boundary")
            baseline = target
            key = (config.user_id, config.project_id)
            if key in readers or config.user in logins:
                raise ValueError("Duplicate scoped audit mapping or login")
            readers[key] = ScopedAuditReader(config)
            logins.add(config.user)
        self._readers = readers

    def ready(self):
        # A revoked or unhealthy configured identity fails readiness closed.
        for reader in self._readers.values():
            reader.ready()
        return True

    def ready_for_project(self, project_id):
        readers = [reader for (_, project), reader in self._readers.items() if project == project_id]
        if not readers:
            raise AppError(503, "AUDIT_UNAVAILABLE", "审计安全配置不可用")
        for reader in readers:
            reader.ready()
        return True

    def audit(self, ctx, **filters):
        reader = self._readers.get((ctx.user_id, ctx.project_id))
        if reader is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        return reader.audit(ctx, **filters)


def scoped_audit_configurations(environ, data_connection):
    """Parse a bounded, explicitly injected secret manifest without echoing it."""
    prefix = "PAPERPILOT_SCOPED_AUDIT_"
    if environ.get(prefix + "ISOLATED_TEST") != "1":
        raise RuntimeError("Explicit isolated scoped audit configuration is required")
    common = [environ.get(prefix + key, "") for key in ("HOST", "PORT", "DBNAME", "OWNER", "BINDING_OWNER")]
    if not all(common):
        raise RuntimeError("Explicit isolated scoped audit configuration is required")
    host, port, dbname, owner, binding_owner = common
    raw = secret_value(environ, prefix + "BINDINGS")
    legacy = [
        secret_value(environ, prefix + key) if key == "PASSWORD" else environ.get(prefix + key, "")
        for key in ("USER", "PASSWORD", "USER_ID", "PROJECT_ID")
    ]

    def reject_duplicates(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("Duplicate manifest key")
            obj[key] = value
        return obj

    try:
        if raw:
            if any(legacy) or len(raw) > 65536:
                raise ValueError("Ambiguous or oversized mapping")
            entries = json.loads(raw, object_pairs_hook=reject_duplicates)
            if not isinstance(entries, list) or not 1 <= len(entries) <= 64:
                raise ValueError("Invalid mapping")
        else:
            if not all(legacy):
                raise ValueError("Missing mapping")
            entries = [dict(zip(("db_user", "db_password", "user_id", "project_id"), legacy, strict=True))]
        if (host, int(port), dbname) != (data_connection.host, data_connection.port, data_connection.dbname):
            raise ValueError("Database target mismatch")
        configs = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {
                "db_user",
                "db_password",
                "user_id",
                "project_id",
            }:
                raise ValueError("Invalid mapping fields")
            if not all(isinstance(value, str) and value.strip() for value in entry.values()):
                raise ValueError("Invalid mapping values")
            if entry["db_user"] == data_connection.user:
                raise ValueError("Reader and business identities must differ")
            config = ScopedAuditConfig(
                host,
                int(port),
                dbname,
                entry["db_user"],
                entry["db_password"],
                UUID(entry["user_id"]),
                UUID(entry["project_id"]),
                owner,
                binding_owner,
            )
            config.validate()
            configs.append(config)
        # Also validates uniqueness before any connection is attempted.
        ScopedAuditRouter(configs)
        return configs
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise RuntimeError("Invalid isolated scoped audit configuration") from None
