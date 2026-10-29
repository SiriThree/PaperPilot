"""Isolated per-user project bootstrap candidate; not wired to application composition."""

from dataclasses import dataclass, field
from pathlib import Path
import re
from uuid import UUID

from app.data.database import ConnectionConfig, PostgresDatabase
from app.errors import AppError
from app.schemas import Project, ProjectCreate

OWNER = "paperpilot_bootstrap_executor"
SOURCE = Path(__file__).parents[1] / "security/migrations/010_candidate_project_bootstrap.sql"
OWNER_INSERT = {
    "projects": {"project_id", "name", "description", "owner_id", "created_at"},
    "project_members": {"project_id", "user_id", "role", "joined_at"},
    "outbox_events": {"event_id", "project_id", "actor_id", "request_id", "payload"},
}
POLICIES = {
    "projects": (
        "bootstrap_project_insert",
        "((owner_id = paperpilot_business.bootstrap_user()) AND (owner_role = 'owner'::text))",
    ),
    "project_members": (
        "bootstrap_member_insert",
        "((user_id = paperpilot_business.bootstrap_user()) AND (role = 'owner'::text))",
    ),
    "outbox_events": ("bootstrap_outbox_insert", "(actor_id = paperpilot_business.bootstrap_user())"),
}


@dataclass(frozen=True)
class ProjectCreatorConfig:
    connection: ConnectionConfig = field(repr=False)
    user_id: UUID
    binding_owner: str
    project_function_owner: str


class ScopedProjectCreator:
    """Bound login may only call fixed creation functions; no user id accepted from SQL."""

    replay_enabled = False

    def __init__(self, config: ProjectCreatorConfig):
        if (
            config.connection.host not in {"127.0.0.1", "localhost", "::1"}
            or not isinstance(config.user_id, UUID)
            or not config.binding_owner
            or not config.project_function_owner
        ):
            raise ValueError("Invalid isolated project creator configuration")
        self.config = config
        self.database = PostgresDatabase(config.connection, pool_size=1)
        self.database.set_transaction_guard(self._verify)

    def _verify(self, connection):
        try:
            self._verify_database(connection)
        except Exception:
            raise AppError(503, "DATA_UNAVAILABLE", "项目创建身份配置不可用") from None

    def _verify_database(self, conn):
        if conn.info.server_version // 10000 != 16:
            raise PermissionError("Unsupported database version")
        roles = conn.execute(
            "SELECT r.rolname, r.rolcanlogin, NOT (r.rolsuper OR r.rolbypassrls OR r.rolcreatedb "
            "OR r.rolcreaterole OR r.rolreplication OR r.rolinherit) AS safe, "
            "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) AS no_members, "
            "NOT has_database_privilege(r.oid,current_database(),'CREATE,TEMP') AS db_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' "
            "AND n.nspname<>'information_schema' AND has_schema_privilege(r.oid,n.oid,'CREATE')) AS schema_ok "
            "FROM pg_roles r WHERE r.rolname=ANY(%s)",
            ([self.config.connection.user, OWNER, self.config.project_function_owner],),
        ).fetchall()
        found = {row["rolname"]: row for row in roles}
        if len({self.config.connection.user, OWNER, self.config.project_function_owner}) != 3 or set(
            found
        ) != {self.config.connection.user, OWNER, self.config.project_function_owner}:
            raise PermissionError("Missing bootstrap identities")
        for name, login in (
            (self.config.connection.user, True),
            (OWNER, False),
            (self.config.project_function_owner, False),
        ):
            row = found[name]
            if row["rolcanlogin"] != login or not all(
                row[k] for k in ("safe", "no_members", "db_ok", "schema_ok")
            ):
                raise PermissionError("Unsafe bootstrap identity")
        identity = conn.execute(
            "SELECT current_user=session_user AND current_user=%s AS ok", (self.config.connection.user,)
        ).fetchone()
        if not identity or not identity["ok"]:
            raise PermissionError("Bootstrap session mismatch")
        grants = conn.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f') "
            "AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            "OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS login_empty, "
            "NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND c.relkind='S' "
            "AND (has_sequence_privilege(current_user,c.oid,'USAGE,SELECT,UPDATE') OR has_sequence_privilege(%s,c.oid,'USAGE,SELECT,UPDATE'))) AS sequences_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND p.prosecdef "
            "AND p.oid NOT IN (to_regprocedure('paperpilot_business.bootstrap_matches(uuid)'), "
            "to_regprocedure('paperpilot_business.bootstrap_create(text,text,uuid)')) "
            "AND NOT (%s AND p.oid=COALESCE(to_regprocedure('paperpilot_business.bootstrap_create_replay(text,text,uuid,text)'),0::oid)) "
            "AND has_function_privilege(current_user,p.oid,'EXECUTE')) AS functions_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND p.prosecdef "
            "AND p.oid NOT IN (to_regprocedure('paperpilot_business.bound_project()'), "
            "to_regprocedure('paperpilot_business.bound_identity(uuid,uuid)'), "
            "to_regprocedure('paperpilot_business.bootstrap_matches(uuid)'), "
            "to_regprocedure('paperpilot_business.bootstrap_create(text,text,uuid)')) "
            "AND NOT (%s AND p.oid=COALESCE(to_regprocedure('paperpilot_business.bootstrap_create_replay(text,text,uuid,text)'),0::oid)) "
            "AND has_function_privilege(%s,p.oid,'EXECUTE')) AS owner_functions_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='pg_catalog' AND p.proname=ANY(ARRAY['pg_read_file','pg_read_binary_file','pg_ls_dir','pg_stat_file']) "
            "AND (has_function_privilege(current_user,p.oid,'EXECUTE') OR has_function_privilege(%s,p.oid,'EXECUTE'))) AS files_ok",
            (OWNER, self.replay_enabled, self.replay_enabled, OWNER, OWNER),
        ).fetchone()
        if not grants or not all(grants.values()):
            raise PermissionError("Bootstrap caller grants invalid")
        binding = conn.execute(
            "SELECT pg_get_userbyid(c.relowner)=%s AS owner_ok, "
            "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a "
            "WHERE a.grantee<>c.relowner AND a.privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER')) AS table_writes_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_attribute a, LATERAL aclexplode(a.attacl) x "
            "WHERE a.attrelid=c.oid AND x.grantee<>c.relowner AND x.privilege_type IN ('INSERT','UPDATE','REFERENCES')) AS column_writes_ok, "
            "EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='p' "
            "AND k.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='login_role' AND attnotnull)]::smallint[]) AS primary_ok, "
            "EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='f' AND k.convalidated "
            "AND k.confrelid='public.users'::regclass AND k.confdeltype='c' "
            "AND k.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='user_id' AND attnotnull)]::smallint[] "
            "AND k.confkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=k.confrelid AND attname='user_id')]::smallint[]) AS foreign_ok "
            "FROM pg_class c WHERE c.oid=to_regclass('paperpilot_business.bootstrap_logins') AND c.relkind='r'",
            (self.config.binding_owner,),
        ).fetchone()
        if not binding or not all(binding.values()):
            raise PermissionError("Bootstrap binding invalid")
        common_source = (
            Path(__file__).parents[1] / "security/migrations/012_candidate_business_active_identity.sql"
        ).read_text(encoding="utf-8-sig")
        expected_common = re.search(
            r"CREATE OR REPLACE FUNCTION paperpilot_business\.bound_project\(\)[^;]*?AS \$\$(.*?)\$\$;",
            common_source,
            re.S,
        )
        common = conn.execute(
            "SELECT p.prosrc,pg_get_userbyid(p.proowner)=%s AS owner_ok,p.prosecdef AS definer, "
            "p.provolatile='s' AS stable,p.proconfig=ARRAY['search_path=pg_catalog'] AS path_ok, "
            "has_function_privilege(%s,p.oid,'EXECUTE') AS callable, "
            "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
            "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS no_public "
            "FROM pg_proc p WHERE p.oid=to_regprocedure('paperpilot_business.bound_project()') "
            "AND p.prorettype='uuid'::regtype AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')",
            (self.config.project_function_owner, OWNER),
        ).fetchone()
        if (
            not expected_common
            or not common
            or common["prosrc"] != expected_common.group(1)
            or not all(
                common[k] for k in ("owner_ok", "definer", "stable", "path_ok", "callable", "no_public")
            )
        ):
            raise PermissionError("Project binding function invalid")
        expected_identity = re.search(
            r"CREATE OR REPLACE FUNCTION paperpilot_business\.bound_identity\([^;]*?AS \$\$(.*?)\$\$;",
            common_source,
            re.S,
        )
        identity = conn.execute(
            "SELECT p.prosrc,pg_get_userbyid(p.proowner)=%s AS owner_ok,p.prosecdef AS definer, "
            "p.provolatile='s' AS stable,p.proconfig=ARRAY['search_path=pg_catalog'] AS path_ok, "
            "has_function_privilege(%s,p.oid,'EXECUTE') AS callable, "
            "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
            "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS no_public "
            "FROM pg_proc p WHERE p.oid=to_regprocedure('paperpilot_business.bound_identity(uuid,uuid)') "
            "AND p.prorettype='boolean'::regtype AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')",
            (self.config.project_function_owner, OWNER),
        ).fetchone()
        if (
            not expected_identity
            or not identity
            or identity["prosrc"] != expected_identity.group(1)
            or not all(
                identity[k] for k in ("owner_ok", "definer", "stable", "path_ok", "callable", "no_public")
            )
        ):
            raise PermissionError("Project identity function invalid")
        common_owner = conn.execute(
            "SELECT has_table_privilege(%s,'paperpilot_business.project_logins','SELECT') AS can_read, "
            "NOT has_table_privilege(%s,'paperpilot_business.project_logins','INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS readonly, "
            "NOT has_any_column_privilege(%s,'paperpilot_business.project_logins','INSERT,UPDATE,REFERENCES') AS columns_readonly, "
            "NOT has_table_privilege(%s,'public.users','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS users_no_table, "
            "NOT has_any_column_privilege(%s,'public.users','INSERT,UPDATE,REFERENCES') AS users_no_write, "
            "has_column_privilege(%s,'public.users','user_id','SELECT') AND "
            "has_column_privilege(%s,'public.users','disabled','SELECT') AND "
            "NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid='public.users'::regclass "
            "AND a.attnum>0 AND NOT a.attisdropped AND a.attname NOT IN ('user_id','disabled') "
            "AND has_column_privilege(%s,a.attrelid,a.attnum,'SELECT')) AS users_columns_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f') "
            "AND c.oid NOT IN ('paperpilot_business.project_logins'::regclass,'public.users'::regclass) "
            "AND (has_table_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            "OR has_any_column_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS no_other_access",
            (self.config.project_function_owner,) * 10,
        ).fetchone()
        if not common_owner or not all(common_owner.values()):
            raise PermissionError("Project binding owner invalid")
        common_binding = conn.execute(
            "SELECT pg_get_userbyid(c.relowner)=%s AS owner_ok, "
            "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a "
            "WHERE a.grantee<>c.relowner AND a.privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER')) AS no_writers, "
            "NOT EXISTS (SELECT 1 FROM pg_attribute a, LATERAL aclexplode(a.attacl) x "
            "WHERE a.attrelid=c.oid AND x.grantee<>c.relowner AND x.privilege_type IN ('INSERT','UPDATE','REFERENCES')) AS no_column_writers "
            "FROM pg_class c WHERE c.oid=to_regclass('paperpilot_business.project_logins') AND c.relkind='r'",
            (self.config.binding_owner,),
        ).fetchone()
        if not common_binding or not all(common_binding.values()):
            raise PermissionError("Project binding table invalid")
        expected_relations = {
            "public.projects",
            "public.project_members",
            "public.outbox_events",
            "public.users",
            "paperpilot_business.bootstrap_logins",
        }
        if self.replay_enabled:
            expected_relations.add("paperpilot_business.bootstrap_requests")
        relations = conn.execute(
            "SELECT n.nspname||'.'||c.relname AS name, "
            "has_table_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS table_access, "
            "has_any_column_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,REFERENCES') AS column_access "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f')",
            (OWNER, OWNER),
        ).fetchall()
        if any(
            row["name"] not in expected_relations and (row["table_access"] or row["column_access"])
            for row in relations
        ):
            raise PermissionError("Bootstrap owner extra relation access")
        for relation, select_cols, insert_cols in (
            ("public.users", {"user_id", "disabled"}, set()),
            ("paperpilot_business.bootstrap_logins", {"login_role", "user_id"}, set()),
            *[("public." + table, set(), cols) for table, cols in OWNER_INSERT.items()],
        ):
            cols = conn.execute(
                "SELECT a.attname, has_column_privilege(%s,a.attrelid,a.attnum,'SELECT') AS rd, "
                "has_column_privilege(%s,a.attrelid,a.attnum,'INSERT') AS ins, "
                "has_column_privilege(%s,a.attrelid,a.attnum,'UPDATE,REFERENCES') AS mut "
                "FROM pg_attribute a WHERE a.attrelid=to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped",
                (OWNER, OWNER, OWNER, relation),
            ).fetchall()
            if (
                {c["attname"] for c in cols if c["rd"]} != select_cols
                or {c["attname"] for c in cols if c["ins"]} != insert_cols
                or any(c["mut"] for c in cols)
            ):
                raise PermissionError("Bootstrap owner column grants invalid")
            forbidden = conn.execute(
                "SELECT NOT has_table_privilege(%s,to_regclass(%s),'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS ok",
                (OWNER, relation),
            ).fetchone()
            if not forbidden or not forbidden["ok"]:
                raise PermissionError("Bootstrap owner write grants invalid")
        for table, (name, expression) in POLICIES.items():
            row = conn.execute(
                "SELECT c.relrowsecurity AND c.relforcerowsecurity AS enforced, "
                "count(p.oid)=2 AS count_ok, bool_and(p.polcmd='a' AND p.polpermissive AND "
                "p.polroles=ARRAY[(SELECT oid FROM pg_roles WHERE rolname=%s)]::oid[] AND p.polqual IS NULL "
                "AND pg_get_expr(p.polwithcheck,p.polrelid)=%s) FILTER (WHERE p.polname=%s) AS extra_ok, "
                "bool_and(p.polcmd='*' AND p.polpermissive AND p.polroles=ARRAY[0::oid] "
                "AND pg_get_expr(p.polqual,p.polrelid)='(project_id = paperpilot_business.bound_project())' "
                "AND pg_get_expr(p.polwithcheck,p.polrelid)=ANY(%s)) "
                "FILTER (WHERE p.polname='project_identity') AS base_ok "
                "FROM pg_class c LEFT JOIN pg_policy p ON p.polrelid=c.oid WHERE c.oid=to_regclass(%s) "
                "GROUP BY c.relrowsecurity,c.relforcerowsecurity",
                (
                    OWNER,
                    expression,
                    name,
                    ["(project_id = paperpilot_business.bound_project())"]
                    + (
                        [
                            "((project_id = paperpilot_business.bound_project()) AND paperpilot_business.bound_identity(actor_id, project_id))"
                        ]
                        if table == "outbox_events"
                        else []
                    ),
                    "public." + table,
                ),
            ).fetchone()
            if not row or not all(row.values()):
                raise PermissionError("Bootstrap policies invalid")
        source = SOURCE.read_text(encoding="utf-8-sig")
        for name, signature, language, security, volatility, result_type in (
            ("bootstrap_user", "paperpilot_business.bootstrap_user()", "sql", False, "s", "uuid"),
            ("bootstrap_matches", "paperpilot_business.bootstrap_matches(uuid)", "sql", True, "s", "boolean"),
            (
                "bootstrap_create",
                "paperpilot_business.bootstrap_create(text,text,uuid)",
                "plpgsql",
                True,
                "v",
                "uuid",
            ),
        ):
            expected = re.search(
                r"CREATE FUNCTION paperpilot_business\." + name + r"\([^;]*?AS \$\$(.*?)\$\$;", source, re.S
            )
            row = conn.execute(
                "SELECT p.prosrc,pg_get_userbyid(p.proowner)=%s AS owner_ok,p.prosecdef=%s AS security_ok, "
                "p.provolatile=%s AS volatility_ok,p.proconfig=ARRAY['search_path=pg_catalog'] AS path_ok, "
                "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
                "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS no_public, "
                "has_function_privilege(current_user,p.oid,'EXECUTE') AS caller_execute "
                "FROM pg_proc p WHERE p.oid=to_regprocedure(%s) AND p.prolang=(SELECT oid FROM pg_language WHERE lanname=%s) "
                "AND pg_get_function_result(p.oid)=%s",
                (OWNER, security, volatility, signature, language, result_type),
            ).fetchone()
            if (
                not expected
                or not row
                or row["prosrc"] != expected.group(1)
                or not all(
                    row[k] for k in ("owner_ok", "security_ok", "volatility_ok", "path_ok", "no_public")
                )
                or row["caller_execute"] != (name != "bootstrap_user")
            ):
                raise PermissionError("Bootstrap function invalid")
        matched = conn.execute(
            "SELECT paperpilot_business.bootstrap_matches(%s) AS matches", (self.config.user_id,)
        ).fetchone()
        if not matched or not matched["matches"]:
            raise PermissionError("Bootstrap identity missing or disabled")
        if self.replay_enabled:
            self._verify_replay(conn)

    def ready(self):
        with self.database.transaction():
            return True

    def create_project(self, user_id, data, request_id):
        if (
            user_id != self.config.user_id
            or not isinstance(data, ProjectCreate)
            or not isinstance(request_id, UUID)
        ):
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        with self.database.transaction(write=True) as conn:
            row = conn.execute(
                "SELECT paperpilot_business.bootstrap_create(%s,%s,%s) AS project_id",
                (data.name, data.description, request_id),
            ).fetchone()
            return Project(
                project_id=row["project_id"],
                name=data.name,
                description=data.description,
                owner_id=user_id,
                role="owner",
                mock=False,
            )

    def close(self):
        self.database.close()


class ScopedReplayProjectCreator(ScopedProjectCreator):
    """Isolated opt-in project replay, with a key distinct from request_id."""

    replay_enabled = True

    def _verify_replay(self, conn):
        row = conn.execute(
            "SELECT pg_get_userbyid(c.relowner)=%s AS owner_ok, "
            "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a "
            "WHERE a.grantee<>c.relowner AND a.grantee<>(SELECT oid FROM pg_roles WHERE rolname=%s)) AS table_acl_ok, "
            "NOT EXISTS (SELECT 1 FROM pg_attribute a, LATERAL aclexplode(a.attacl) x "
            "WHERE a.attrelid=c.oid AND x.grantee<>c.relowner AND "
            "(x.grantee<>(SELECT oid FROM pg_roles WHERE rolname=%s) OR x.privilege_type NOT IN ('SELECT','INSERT'))) AS column_acl_ok, "
            "EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='p' "
            "AND k.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='user_id' AND attnotnull), "
            "(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='operation_key' AND attnotnull)]::smallint[]) AS primary_ok, "
            "EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='f' AND k.convalidated "
            "AND k.condeferrable AND k.condeferred AND k.confrelid=to_regclass('public.projects') "
            "AND k.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='project_id' AND attnotnull)]::smallint[] "
            "AND k.confkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=k.confrelid AND attname='project_id')]::smallint[]) AS project_fk_ok "
            ", EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='f' AND k.convalidated "
            "AND k.confrelid=to_regclass('public.users') "
            "AND k.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='user_id' AND attnotnull)]::smallint[] "
            "AND k.confkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=k.confrelid AND attname='user_id')]::smallint[]) AS user_fk_ok "
            "FROM pg_class c WHERE c.oid=to_regclass('paperpilot_business.bootstrap_requests') AND c.relkind='r'",
            (self.config.binding_owner, OWNER, OWNER),
        ).fetchone()
        if not row or not all(row.values()):
            raise PermissionError("Project replay table invalid")
        columns = conn.execute(
            "SELECT a.attname,has_column_privilege(%s,a.attrelid,a.attnum,'SELECT') AS rd, "
            "has_column_privilege(%s,a.attrelid,a.attnum,'INSERT') AS ins, "
            "has_column_privilege(%s,a.attrelid,a.attnum,'UPDATE,REFERENCES') AS mut "
            "FROM pg_attribute a WHERE a.attrelid=to_regclass('paperpilot_business.bootstrap_requests') "
            "AND a.attnum>0 AND NOT a.attisdropped",
            (OWNER, OWNER, OWNER),
        ).fetchall()
        expected_columns = {"user_id", "operation_key", "project_id", "name", "description"}
        if (
            {c["attname"] for c in columns if c["rd"]} != expected_columns
            or {c["attname"] for c in columns if c["ins"]} != expected_columns
            or any(c["mut"] for c in columns)
        ):
            raise PermissionError("Project replay grants invalid")
        forbidden = conn.execute(
            "SELECT NOT has_table_privilege(%s,to_regclass('paperpilot_business.bootstrap_requests'),"
            "'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS ok",
            (OWNER,),
        ).fetchone()
        if not forbidden or not forbidden["ok"]:
            raise PermissionError("Project replay mutation grants invalid")
        source = (SOURCE.parent / "011_candidate_project_replay.sql").read_text(encoding="utf-8-sig")
        expected = re.search(
            r"CREATE FUNCTION paperpilot_business\.bootstrap_create_replay\([^;]*?AS \$\$(.*?)\$\$;",
            source,
            re.S,
        )
        func = conn.execute(
            "SELECT p.prosrc,pg_get_userbyid(p.proowner)=%s AS owner_ok,p.prosecdef AS definer, "
            "p.provolatile='v' AS volatile,p.proconfig=ARRAY['search_path=pg_catalog'] AS path_ok, "
            "has_function_privilege(current_user,p.oid,'EXECUTE') AS callable, "
            "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
            "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS no_public "
            "FROM pg_proc p WHERE p.oid=to_regprocedure('paperpilot_business.bootstrap_create_replay(text,text,uuid,text)') "
            "AND p.prorettype='uuid'::regtype AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')",
            (OWNER,),
        ).fetchone()
        if (
            not expected
            or not func
            or func["prosrc"] != expected.group(1)
            or not all(
                func[k] for k in ("owner_ok", "definer", "volatile", "path_ok", "callable", "no_public")
            )
        ):
            raise PermissionError("Project replay function invalid")

    def create_project_with_key(self, user_id, data, request_id, operation_key):
        if (
            user_id != self.config.user_id
            or not isinstance(data, ProjectCreate)
            or not isinstance(request_id, UUID)
            or not isinstance(operation_key, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", operation_key)
        ):
            raise AppError(400, "VALIDATION_ERROR", "项目创建请求格式不正确")
        with self.database.transaction(write=True) as conn:
            row = conn.execute(
                "SELECT paperpilot_business.bootstrap_create_replay(%s,%s,%s,%s) AS project_id",
                (data.name, data.description, request_id, operation_key),
            ).fetchone()
            return Project(
                project_id=row["project_id"],
                name=data.name,
                description=data.description,
                owner_id=user_id,
                role="owner",
                mock=False,
            )
