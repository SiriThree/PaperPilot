"""One-shot operator commands for the separately named local production database."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
from uuid import UUID, uuid4

from argon2 import PasswordHasher
import psycopg
from psycopg import sql

from app.adapters.ports import Context
from app.data.adapter import PostgresData
from app.data.business_boundary import BUSINESS_TABLE_PRIVILEGES
from app.data.database import ConnectionConfig, PostgresDatabase
from app.data.settings import DataSettings
from app.schemas import ProjectCreate
from app.security.provision import install, create_login

STATE = Path("/state")
RUNTIME = Path("/runtime")


def admin_config():
    return ConnectionConfig(
        "postgres",
        5432,
        "postgres",
        Path("/run/secrets/postgres_admin_password").read_text().strip(),
        "paperpilot_production",
    )


def save(path, value, *, app=False):
    content = json.dumps(value, ensure_ascii=False, indent=2) if not isinstance(value, str) else value
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    if app:
        os.chown(temporary, 10001, 10001)
    temporary.replace(path)


def role(conn, name, password=None):
    statement = (
        "CREATE ROLE {} "
        + ("LOGIN" if password else "NOLOGIN")
        + " NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
    )
    if password:
        verifier = conn.pgconn.encrypt_password(password.encode(), name.encode(), b"scram-sha-256").decode()
        conn.execute(sql.SQL(statement + " PASSWORD {}").format(sql.Identifier(name), sql.Literal(verifier)))
    else:
        conn.execute(sql.SQL(statement).format(sql.Identifier(name)))


def grant(conn, statement, name):
    conn.execute(sql.SQL(statement).format(sql.Identifier(name)))


def provision():
    STATE.mkdir(exist_ok=True)
    RUNTIME.mkdir(exist_ok=True)
    meta_path = STATE / "deployment.json"
    meta = json.loads(meta_path.read_text())
    config = admin_config()
    with psycopg.connect(**config.conninfo(), autocommit=True) as conn:
        marker = conn.execute(
            "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"
        ).fetchone()[0]
        expected = "PaperPilot local production " + meta["deployment_id"]
        if marker not in (None, expected):
            raise ValueError("Database belongs to a different deployment")
        if marker is None:
            if conn.execute("SELECT EXISTS(SELECT 1 FROM pg_tables WHERE schemaname='public')").fetchone()[0]:
                raise ValueError("Refusing to adopt an existing database")
            conn.execute(
                sql.SQL("COMMENT ON DATABASE paperpilot_production IS {}").format(sql.Literal(expected))
            )
    db = PostgresDatabase(config)
    try:
        db.migrate()
        if (RUNTIME / "runtime.json").is_file():
            stored = json.loads((RUNTIME / "runtime.json").read_text())
            if stored["deployment_id"] != meta["deployment_id"]:
                raise ValueError("Runtime belongs to a different deployment")
            print("Existing owned production configuration retained")
            return
        with psycopg.connect(**config.conninfo(), autocommit=True) as conn:
            if conn.execute("SELECT to_regclass('paperpilot_security.service_logins')").fetchone()[0] is None:
                install(conn)
        suffix = meta["deployment_id"].replace("-", "")[:12]
        accounts = {
            name: {"user_id": str(uuid4()), "username": username, "password": secrets.token_urlsafe(24)}
            for name, username in (
                ("owner", "owner"),
                ("member", "accept_member_" + suffix),
                ("outsider", "accept_outsider_" + suffix),
            )
        }
        with db.transaction(write=True) as conn:
            for name, entry in accounts.items():
                conn.execute(
                    "INSERT INTO public.users(user_id,username,display_name,password_hash) VALUES(%s,%s,%s,%s)",
                    (
                        entry["user_id"],
                        entry["username"],
                        "PaperPilot owner" if name == "owner" else "Disabled after security acceptance",
                        PasswordHasher().hash(entry["password"]),
                    ),
                )
        data = PostgresData(db, DataSettings())
        projects = {}
        for key, owner, name in (
            ("main", "owner", "PaperPilot"),
            ("accept_a", "owner", "安全验收样例 A（非研究数据）"),
            ("accept_b", "outsider", "安全验收隔离项目 B"),
        ):
            projects[key] = str(
                data.create_project(
                    UUID(accounts[owner]["user_id"]), ProjectCreate(name=name), uuid4()
                ).project_id
            )
        ctx = Context(UUID(accounts["owner"]["user_id"]), UUID(projects["accept_a"]), uuid4())
        data.add_member(ctx, accounts["member"]["username"])
        with db.transaction(write=True) as conn:
            for label, owner in (("accept_a", "owner"), ("accept_b", "outsider")):
                metadata, paper = uuid4(), uuid4()
                conn.execute(
                    "INSERT INTO papers(metadata_id,project_id,title,year) VALUES(%s,%s,%s,2026)",
                    (metadata, projects[label], "权限验收标记（非论文正文）"),
                )
                conn.execute(
                    "INSERT INTO project_papers(paper_id,project_id,metadata_id,imported_by,digest,storage_key) VALUES(%s,%s,%s,%s,%s,%s)",
                    (
                        paper,
                        projects[label],
                        metadata,
                        accounts[owner]["user_id"],
                        hashlib.sha256(label.encode()).hexdigest(),
                        f"acceptance/{paper}.pdf",
                    ),
                )
        identities = {
            name: {"user": f"pp_{kind}_{suffix}", "password": secrets.token_urlsafe(36)}
            for name, kind in (
                ("business", "business"),
                ("authentication", "auth"),
                ("audit", "audit"),
                ("outbox", "outbox"),
            )
        }
        audit_owner, auth_owner = "pp_audit_owner_" + suffix, "pp_auth_lock_" + suffix
        readers = [
            {
                "user": f"pp_reader_{suffix}_{index}",
                "password": secrets.token_urlsafe(36),
                "project_id": project,
            }
            for index, project in enumerate(projects.values())
        ]
        with psycopg.connect(**config.conninfo(), autocommit=True) as conn:
            with conn.transaction():
                role(conn, audit_owner)
                grant(conn, "GRANT USAGE ON SCHEMA public,paperpilot_audit TO {}", audit_owner)
                grant(conn, "GRANT SELECT ON public.audit_events TO {}", audit_owner)
                grant(conn, "GRANT CREATE ON SCHEMA paperpilot_audit TO {}", audit_owner)
                grant(conn, "ALTER VIEW paperpilot_audit.project_events OWNER TO {}", audit_owner)
                grant(
                    conn, "ALTER FUNCTION paperpilot_audit.event_fingerprint(uuid) OWNER TO {}", audit_owner
                )
                grant(conn, "REVOKE CREATE ON SCHEMA paperpilot_audit FROM {}", audit_owner)
                source = Path(__file__).parent / "security/migrations/004_auth_lock_candidate.sql"
                conn.execute(source.read_text())
                role(conn, auth_owner)
                grant(conn, "GRANT USAGE ON SCHEMA public,paperpilot_auth TO {}", auth_owner)
                grant(
                    conn,
                    "GRANT SELECT(user_id,password_hash,disabled),UPDATE(password_hash) ON public.users TO {}",
                    auth_owner,
                )
                grant(conn, "GRANT CREATE ON SCHEMA paperpilot_auth TO {}", auth_owner)
                grant(conn, "ALTER FUNCTION paperpilot_auth.lock_credential(uuid) OWNER TO {}", auth_owner)
                grant(conn, "REVOKE CREATE ON SCHEMA paperpilot_auth FROM {}", auth_owner)
                for key in ("business", "authentication", "outbox"):
                    login = identities[key]
                    role(conn, login["user"], login["password"])
                    grant(conn, "GRANT USAGE ON SCHEMA public TO {}", login["user"])
                business = identities["business"]["user"]
                for table, privileges in BUSINESS_TABLE_PRIVILEGES.items():
                    conn.execute(
                        sql.SQL("GRANT {} ON public.{} TO {}").format(
                            sql.SQL(",").join(sql.SQL(p) for p in sorted(privileges)),
                            sql.Identifier(table),
                            sql.Identifier(business),
                        )
                    )
                grant(conn, "GRANT SELECT(user_id,username,display_name) ON public.users TO {}", business)
                grant(conn, "GRANT USAGE ON SCHEMA paperpilot_audit TO {}", business)
                grant(conn, "GRANT SELECT ON paperpilot_audit.project_events TO {}", business)
                grant(
                    conn, "GRANT EXECUTE ON FUNCTION paperpilot_audit.event_fingerprint(uuid) TO {}", business
                )
                auth = identities["authentication"]["user"]
                grant(conn, "GRANT USAGE ON SCHEMA paperpilot_auth TO {}", auth)
                grant(
                    conn, "GRANT SELECT(user_id,username,password_hash,disabled) ON public.users TO {}", auth
                )
                grant(
                    conn,
                    "GRANT SELECT,INSERT,UPDATE ON public.auth_sessions,public.auth_login_limits TO {}",
                    auth,
                )
                grant(conn, "GRANT INSERT ON public.audit_events TO {}", auth)
                grant(conn, "GRANT EXECUTE ON FUNCTION paperpilot_auth.lock_credential(uuid) TO {}", auth)
                grant(conn, "GRANT SELECT,UPDATE ON public.outbox_events TO {}", identities["outbox"]["user"])
            create_login(conn, identities["audit"]["user"], identities["audit"]["password"])
            for reader in readers:
                create_login(conn, reader["user"], reader["password"], project_id=UUID(reader["project_id"]))
            for login in [*identities.values(), *readers]:
                grant(conn, "ALTER ROLE {} SET statement_timeout='5s'", login["user"])
                grant(conn, "ALTER ROLE {} SET lock_timeout='2s'", login["user"])
                grant(conn, "ALTER ROLE {} SET idle_in_transaction_session_timeout='10s'", login["user"])
        runtime = {
            "format": 1,
            "deployment_id": meta["deployment_id"],
            "host": "postgres",
            "port": 5432,
            "dbname": config.dbname,
            "audit_owner": audit_owner,
            "auth_lock_owner": auth_owner,
            **identities,
            "readers": readers,
        }
        save(RUNTIME / "runtime.json", runtime, app=True)
        meta.update(
            {
                "projects": projects,
                "users": {k: {p: v[p] for p in ("user_id", "username")} for k, v in accounts.items()},
                "roles": {k: v["user"] for k, v in identities.items()},
                "audit_owner": audit_owner,
                "auth_lock_owner": auth_owner,
                "initialized_at": datetime.now(UTC).isoformat(),
            }
        )
        save(meta_path, meta)
        save(STATE / "acceptance-secrets.json", accounts)
        save(
            STATE / "owner-credentials.txt",
            f"URL: {meta['url']}\nUsername: owner\nPassword: {accounts['owner']['password']}\n",
        )
        print("Production identities provisioned; owner credentials saved to the protected local file")
    finally:
        db.close()


def qa(active):
    meta = json.loads((STATE / "deployment.json").read_text())
    with psycopg.connect(**admin_config().conninfo(), autocommit=True) as conn:
        for key in ("member", "outsider"):
            user = meta["users"][key]["user_id"]
            conn.execute("UPDATE public.users SET disabled=%s WHERE user_id=%s", (not active, user))
            if not active:
                conn.execute(
                    "UPDATE auth_sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL",
                    (user,),
                )
        if active:
            conn.execute(
                "INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,'member') ON CONFLICT DO NOTHING",
                (meta["projects"]["accept_a"], meta["users"]["member"]["user_id"]),
            )
    print(
        "Acceptance-only accounts enabled"
        if active
        else "Acceptance-only accounts disabled and sessions revoked"
    )


def configure_sql_model():
    from app.production import SQLModel

    path = STATE / "sql-model.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("Invalid staged SQL model configuration")
    model = SQLModel.model_validate_json(path.read_bytes())
    runtime_path = RUNTIME / "runtime.json"
    runtime = json.loads(runtime_path.read_text())
    meta_path = STATE / "deployment.json"
    meta = json.loads(meta_path.read_text())
    if runtime["deployment_id"] != meta["deployment_id"]:
        raise ValueError("Runtime deployment mismatch")
    runtime["sql_model"] = {**model.model_dump(mode="json"), "api_key": model.api_key.get_secret_value()}
    save(runtime_path, runtime, app=True)
    meta["statistics_sql_mode"] = "model"
    meta["sql_model_fingerprint"] = model.fingerprint()
    save(meta_path, meta)
    print("Protected production SQL model configured; C research AI remains disabled")


def audit_drift(enabled):
    runtime = json.loads((RUNTIME / "runtime.json").read_text())
    with psycopg.connect(**admin_config().conninfo(), autocommit=True) as conn:
        grant(
            conn,
            (
                "GRANT SELECT ON public.audit_events TO {}"
                if enabled
                else "REVOKE SELECT ON public.audit_events FROM {}"
            ),
            runtime["audit"]["user"],
        )
    print("Controlled audit grant drift enabled" if enabled else "Controlled audit grant drift repaired")


def database_checks():
    runtime = json.loads((RUNTIME / "runtime.json").read_text())
    meta = json.loads((STATE / "deployment.json").read_text())
    target = next(r for r in runtime["readers"] if r["project_id"] == meta["projects"]["accept_a"])
    results = {}
    base = admin_config()
    with psycopg.connect(
        host=base.host,
        port=base.port,
        dbname=base.dbname,
        user=target["user"],
        password=target["password"],
        autocommit=True,
    ) as conn:
        results["foreign_project_zero_rows"] = not conn.execute(
            "SELECT paper_id FROM paperpilot_security.paper_statistics WHERE project_id=%s",
            (meta["projects"]["accept_b"],),
        ).fetchall()
        for name, statement in (
            ("base_table_denied", "SELECT title FROM public.papers"),
            ("write_denied", "UPDATE public.projects SET name='must never commit'"),
        ):
            conn.execute("BEGIN")
            try:
                conn.execute(statement)
                results[name] = False
            except psycopg.Error as error:
                results[name] = error.sqlstate == "42501"
            finally:
                conn.rollback()
    business = runtime["business"]
    with psycopg.connect(
        host=base.host,
        port=base.port,
        dbname=base.dbname,
        user=business["user"],
        password=business["password"],
        autocommit=True,
    ) as conn:
        for name, statement in (
            ("business_hash_denied", "SELECT password_hash FROM public.users"),
            ("business_sessions_denied", "SELECT * FROM public.auth_sessions"),
            ("business_audit_update_denied", "UPDATE public.audit_events SET action='must never commit'"),
        ):
            conn.execute("BEGIN")
            try:
                conn.execute(statement)
                results[name] = False
            except psycopg.Error as error:
                results[name] = error.sqlstate == "42501"
            finally:
                conn.rollback()
    print(json.dumps(results))
    if not all(results.values()):
        raise ValueError("Production native permission checks failed")


def clear_rate_limit(peer):
    with psycopg.connect(**admin_config().conninfo(), autocommit=True) as conn:
        conn.execute(
            "DELETE FROM public.auth_login_limits WHERE peer_hash=%s",
            (hashlib.sha256(peer.encode()).digest(),),
        )
    print("Acceptance peer rate counter cleared")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "provision",
            "configure-sql-model",
            "qa-enable",
            "qa-disable",
            "audit-drift",
            "audit-repair",
            "database-checks",
            "clear-rate",
        ],
    )
    parser.add_argument("--peer")
    args = parser.parse_args()
    try:
        if args.command == "provision":
            provision()
        elif args.command == "configure-sql-model":
            configure_sql_model()
        elif args.command in {"qa-enable", "qa-disable"}:
            qa(args.command == "qa-enable")
        elif args.command in {"audit-drift", "audit-repair"}:
            audit_drift(args.command == "audit-drift")
        elif args.command == "database-checks":
            database_checks()
        elif args.command == "clear-rate":
            import ipaddress

            clear_rate_limit(str(ipaddress.ip_address(args.peer)))
    except Exception as error:
        raise SystemExit("Production setup failed: " + type(error).__name__) from None


if __name__ == "__main__":
    main()
