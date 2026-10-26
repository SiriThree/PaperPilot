"""Four real PG16 preflight checks; never an implementation of the 26-method DataPort."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import secrets
import sys
from uuid import uuid4

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.data.settings import EMBEDDING_DIM, DataSettings  # noqa: E402


def ensure(condition, message):
    if not condition:
        raise RuntimeError(message)


def local_config():
    values = {}
    path = ROOT / ".runtime/day0.env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values[key] = value
    password = os.getenv("PAPERPILOT_DAY0_PASSWORD") or values.get("PAPERPILOT_DAY0_PASSWORD")
    ensure(password, "Run start-postgres-day0.ps1 or supply PAPERPILOT_DAY0_PASSWORD first")
    return dict(
        host="127.0.0.1",
        port=int(os.getenv("PAPERPILOT_DAY0_PORT", values.get("PAPERPILOT_DAY0_PORT", "54326"))),
        dbname="paperpilot_day0_preflight",
        user="postgres",
        password=password,
        connect_timeout=10,
        autocommit=True,
    )


def denied(connection, statement, params=None):
    try:
        connection.execute(statement, params)
    except psycopg.errors.InsufficientPrivilege:
        return True
    raise RuntimeError("Expected database permission rejection")


def verify(report):
    config = local_config()
    suffix = uuid4().hex[:12]
    schema = "day0_" + suffix
    roles = ["pp_" + name + "_" + suffix for name in ("a", "b", "ro")]
    passwords = [secrets.token_urlsafe(32) for _ in roles]
    created_roles = []
    created_schema = False
    table = sql.Identifier(schema, "chunks")
    ledger = sql.Identifier(schema, "ledger")
    vector_a = json.dumps([1.0] + [0.0] * (EMBEDDING_DIM - 1))
    vector_far = json.dumps([0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2))
    settings = DataSettings.from_env()
    with psycopg.connect(**config) as admin:
        ensure(160000 <= admin.info.server_version < 170000, "PostgreSQL 16 is required")
        ensure(EMBEDDING_DIM >= 2, "Preflight needs at least two dimensions")
        admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        report.update(
            postgres=admin.execute("SHOW server_version").fetchone()[0],
            psycopg=psycopg.__version__,
            pgvector=admin.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[
                0
            ],
            embedding_dim=EMBEDDING_DIM,
            embedding_status="synthetic placeholder; MockAI has no embeddings; awaiting member C",
            lease_seconds=settings.lease_seconds,
            idempotency_retention_seconds=settings.idempotency_retention_seconds,
        )
        try:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created_schema = True
            admin.execute(sql.SQL("CREATE TABLE {} (id integer PRIMARY KEY)").format(ledger))
            with admin.transaction():
                admin.execute(sql.SQL("INSERT INTO {} VALUES (1)").format(ledger))
            try:
                with admin.transaction():
                    admin.execute(sql.SQL("INSERT INTO {} VALUES (2)").format(ledger))
                    raise ValueError("intentional rollback")
            except ValueError:
                pass
            ensure(
                admin.execute(sql.SQL("SELECT id FROM {} ORDER BY id").format(ledger)).fetchall() == [(1,)],
                "Commit/rollback verification failed",
            )
            report["checks"]["psycopg3_transactions"] = {"passed": True, "committed": [1], "rolled_back": [2]}

            admin.execute(
                sql.SQL(
                    "CREATE TABLE {} (project_id text NOT NULL, label text PRIMARY KEY, embedding vector({}))"
                ).format(table, sql.Literal(EMBEDDING_DIM))
            )
            for project, label, vector in [
                ("project_a", "a-near", vector_a),
                ("project_a", "a-far", vector_far),
                ("project_b", "b-near", vector_a),
            ]:
                admin.execute(
                    sql.SQL("INSERT INTO {} VALUES (%s,%s,%s::vector)").format(table),
                    (project, label, vector),
                )
            nearest = admin.execute(
                sql.SQL(
                    "SELECT label, 1 - (embedding <=> %s::vector) AS similarity "
                    "FROM {} WHERE project_id='project_a' ORDER BY embedding <=> %s::vector"
                ).format(table),
                (vector_a, vector_a),
            ).fetchall()
            ensure(nearest[0] == ("a-near", 1.0) and nearest[1][1] == 0.0, "Vector cosine ordering failed")
            report["checks"]["vector_similarity"] = {"passed": True, "ranking": nearest}

            # Real login roles, not SET ROLE under a superuser connection.
            for role, password in zip(roles, passwords):
                admin.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS"
                    ).format(sql.Identifier(role), sql.Literal(password))
                )
                created_roles.append(role)
                admin.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                        sql.Identifier(schema), sql.Identifier(role)
                    )
                )
                admin.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(table, sql.Identifier(role)))
            for role in roles[:2]:
                admin.execute(
                    sql.SQL("GRANT INSERT, UPDATE, DELETE ON {} TO {}").format(table, sql.Identifier(role))
                )
            admin.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(table))
            admin.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(table))
            policy = sql.SQL(
                "CREATE POLICY project_scope ON {} USING (project_id = CASE current_user "
                "WHEN {} THEN 'project_a' WHEN {} THEN 'project_b' WHEN {} THEN 'project_a' END)"
            ).format(table, *[sql.Literal(role) for role in roles])
            admin.execute(policy)
            isolation = []
            for role, password, project, count in zip(
                roles[:2], passwords[:2], ["project_a", "project_b"], [2, 1]
            ):
                with psycopg.connect(**(config | {"user": role, "password": password})) as conn:
                    ensure(
                        conn.execute(
                            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                        ).fetchone()
                        == (False,),
                        "Test role must not bypass RLS",
                    )
                    rows = conn.execute(sql.SQL("SELECT project_id FROM {}").format(table)).fetchall()
                    ensure(
                        len(rows) == count and all(row[0] == project for row in rows),
                        "RLS isolation failed without application filtering",
                    )
                    other = "project_b" if project == "project_a" else "project_a"
                    # Authorized writes must succeed: isolation must not merely reject all writes.
                    conn.execute(
                        sql.SQL("INSERT INTO {} VALUES (%s,%s,%s::vector)").format(table),
                        (project, role + "-allowed", vector_a),
                    )
                    conn.execute(sql.SQL("DELETE FROM {} WHERE label=%s").format(table), (role + "-allowed",))
                    ensure(
                        not conn.execute(
                            sql.SQL("SELECT label FROM {} WHERE project_id=%s").format(table), (other,)
                        ).fetchall(),
                        "Cross-project read escaped RLS",
                    )
                    denied(
                        conn,
                        sql.SQL("INSERT INTO {} VALUES (%s,%s,%s::vector)").format(table),
                        (other, role + "-blocked", vector_a),
                    )
                    isolation.append(
                        {
                            "project": project,
                            "visible_rows": count,
                            "own_write_allowed": True,
                            "cross_write_denied": True,
                        }
                    )
            report["checks"]["rls_two_login_roles"] = {"passed": True, "roles": isolation}

            # Database is the dedicated compose preflight database, never the application database.
            admin.execute("REVOKE CREATE, TEMPORARY ON DATABASE paperpilot_day0_preflight FROM PUBLIC")
            admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            readonly = roles[2]
            admin.execute(
                sql.SQL("ALTER ROLE {} SET default_transaction_read_only=on").format(sql.Identifier(readonly))
            )
            with psycopg.connect(**(config | {"user": readonly, "password": passwords[2]})) as conn:
                # Prove ACLs still reject writes when the session read-only default is turned off.
                conn.execute("SET default_transaction_read_only=off")
                ensure(
                    conn.execute(sql.SQL("SELECT count(*) FROM {}").format(table)).fetchone() == (2,),
                    "Read-only role cannot read its authorized rows",
                )
                statements = {
                    "insert": sql.SQL("INSERT INTO {} VALUES ('project_a','blocked','{}'::vector)").format(
                        table, sql.SQL(vector_a)
                    ),
                    "update": sql.SQL("UPDATE {} SET label='blocked' WHERE label='a-near'").format(table),
                    "delete": sql.SQL("DELETE FROM {} WHERE label='a-near'").format(table),
                    "create_table": sql.SQL("CREATE TABLE {} (id int)").format(
                        sql.Identifier(schema, "blocked")
                    ),
                    "create_public_table": sql.SQL("CREATE TABLE public.{} (id int)").format(
                        sql.Identifier(schema)
                    ),
                    "create_temp": sql.SQL("CREATE TEMP TABLE {} (id int)").format(sql.Identifier(schema)),
                }
                for statement in statements.values():
                    denied(conn, statement)
                denied(conn, sql.SQL("SET ROLE {}").format(sql.Identifier(roles[0])))
            report["checks"]["readonly_no_write"] = {
                "passed": True,
                "denied": list(statements) + ["assume_business_role"],
                "tested_with_readonly_default_disabled": True,
            }
        finally:
            if created_schema:
                admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            for role in created_roles:
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "docs/day0-verification.json")
    args = parser.parse_args()
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "checks": {}, "passed": False}
    try:
        verify(report)
        report["passed"] = len(report["checks"]) == 4
    except Exception as exc:
        # Connection exceptions can contain host details. Never print a DSN or credentials.
        report["error"] = {"type": type(exc).__name__, "sqlstate": getattr(exc, "sqlstate", None)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
