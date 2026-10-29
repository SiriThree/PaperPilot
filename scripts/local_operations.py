"""Private local backups, isolated restore drills, conservative retention and file-based alerts."""

import argparse
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from uuid import UUID, uuid4

from local_deployment import (
    STATE,
    admin_config,
    configuration,
    container,
    private_directory,
    run,
    running,
    start,
    stop,
    write_json,
    operator_lock,
)


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Backup links are refused")
        relative = path.relative_to(directory).as_posix()
        if path.is_file() and relative not in {"manifest.json", "manifest.hmac"}:
            result[relative] = file_hash(path)
    return result


def database_snapshot(config):
    import psycopg
    from psycopg import sql

    result = {}
    with psycopg.connect(**config.conninfo()) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        tables = connection.execute(
            "SELECT schemaname,tablename FROM pg_tables WHERE schemaname IN ('public','paperpilot_security','paperpilot_auth') ORDER BY 1,2"
        ).fetchall()
        for schema, table in tables:
            if (schema, table) == ("paperpilot_auth", "sessions"):
                continue  # Restore intentionally invalidates every session instead of reviving old cookies.
            records = connection.execute(
                sql.SQL("SELECT row_to_json(t) FROM {}.{} t").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            ).fetchall()
            hashes = []
            for (record,) in records:
                if (schema, table) == ("paperpilot_security", "service_logins"):
                    record.pop("role_oid")  # Cluster-assigned OIDs are rebound explicitly after restore.
                hashes.append(
                    hashlib.sha256(
                        json.dumps(record, sort_keys=True, ensure_ascii=False).encode()
                    ).hexdigest()
                )
            result[schema + "." + table] = {
                "rows": len(hashes),
                "sha256": hashlib.sha256("".join(sorted(hashes)).encode()).hexdigest(),
            }
    return result


def integrity_key():
    path = STATE / "secrets/backup-integrity"
    if not path.exists():
        with path.open("x", encoding="ascii") as stream:
            stream.write(secrets.token_hex(32))
    return bytes.fromhex(path.read_text(encoding="ascii").strip())


def verify_backup(path):
    path = path.resolve(strict=True)
    if not path.is_relative_to((STATE / "backups").resolve()) or path.is_symlink():
        raise ValueError("Use an owned local backup directory")
    body = (path / "manifest.json").read_bytes()
    if not hmac.compare_digest(
        hmac.new(integrity_key(), body, "sha256").hexdigest(),
        (path / "manifest.hmac").read_text(encoding="ascii"),
    ):
        raise ValueError("Backup integrity check failed")
    manifest = json.loads(body)
    if manifest["files"] != inventory(path):
        raise ValueError("Backup files are missing or changed")
    return manifest


def backup():
    configuration()
    was_running = running()
    stop()
    directory = STATE / "backups" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    private_directory(directory)
    try:
        snapshot = database_snapshot(admin_config())
        upload_snapshot = inventory(STATE / "uploads")
        identifier = container()
        for filename, arguments in (
            ("database.dump", ["pg_dump", "-U", "postgres", "-d", "paperpilot_local", "--format=custom"]),
            ("roles.sql", ["pg_dumpall", "-U", "postgres", "--roles-only"]),
        ):
            with (directory / filename).open("wb") as output:
                subprocess.run(
                    ["docker", "exec", identifier, *arguments],
                    check=True,
                    stdout=output,
                    stderr=subprocess.PIPE,
                )
        for name in ("uploads", "secrets", "tls"):
            shutil.copytree(STATE / name, directory / name, ignore=shutil.ignore_patterns("backup-integrity"))
        for name in ("deployment.json", "security.json"):
            shutil.copy2(STATE / name, directory / name)
        # An out-of-process writer must not produce a silently inconsistent DB/files backup.
        if (
            database_snapshot(admin_config()) != snapshot
            or inventory(STATE / "uploads") != upload_snapshot
            or inventory(directory / "uploads") != upload_snapshot
        ):
            raise RuntimeError("Database changed during maintenance backup; backup not confirmed")
        model = configuration().get("ai_config")
        manifest = {
            "format": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "snapshot": snapshot,
            "files": inventory(directory),
            "external_model_config": model,
            "scope": "local ACL-protected recovery copy; off-machine encryption/escrow remains operator work",
        }
        write_json(directory / "manifest.json", manifest)
        (directory / "manifest.hmac").write_text(
            hmac.new(integrity_key(), (directory / "manifest.json").read_bytes(), "sha256").hexdigest(),
            encoding="ascii",
        )
        verify_backup(directory)
        print("Local backup verified:", directory)
        return directory
    finally:
        if was_running:
            start()


def restore_drill(directory):
    manifest = verify_backup(directory)
    identifier = "paperpilot-restore-" + uuid4().hex[:16]
    image = "pgvector/pgvector@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a"
    created = False
    try:
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                identifier,
                "--label",
                "paperpilot.disposable-restore=true",
                "-e",
                "POSTGRES_PASSWORD_FILE=/run/secrets/postgres",
                "-e",
                "POSTGRES_DB=paperpilot_local",
                "--mount",
                f"type=bind,source={directory / 'secrets/postgres'},target=/run/secrets/postgres,readonly",
                "-p",
                "127.0.0.1::5432",
                image,
            ]
        )
        created = True
        for _ in range(60):
            try:
                run(["docker", "exec", identifier, "pg_isready", "-U", "postgres", "-d", "paperpilot_local"])
                break
            except subprocess.CalledProcessError:
                time.sleep(1)
        else:
            raise RuntimeError("Restore container never became ready")
        roles = (directory / "roles.sql").read_text(encoding="utf-8")
        if roles.count("CREATE ROLE postgres;") != 1:
            raise ValueError("Unexpected bootstrap administrator in role backup")
        run(
            [
                "docker",
                "exec",
                "-i",
                identifier,
                "psql",
                "-U",
                "postgres",
                "-d",
                "paperpilot_local",
                "-v",
                "ON_ERROR_STOP=1",
            ],
            input=roles.replace("CREATE ROLE postgres;", "").encode(),
        )
        with (directory / "database.dump").open("rb") as source:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    identifier,
                    "pg_restore",
                    "-U",
                    "postgres",
                    "-d",
                    "paperpilot_local",
                    "--exit-on-error",
                ],
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        port = int(
            run(["docker", "port", identifier, "5432/tcp"], text=True).stdout.strip().rsplit(":", 1)[1]
        )
        config = replace(admin_config(port), password=(directory / "secrets/postgres").read_text().strip())
        import psycopg

        with psycopg.connect(**config.conninfo()) as admin:
            admin.execute("REVOKE CREATE,TEMPORARY ON DATABASE paperpilot_local FROM PUBLIC")
            admin.execute(
                "CREATE TEMP TABLE restored_registry AS SELECT * FROM paperpilot_security.service_logins"
            )
            count = admin.execute("DELETE FROM paperpilot_security.service_logins").rowcount
            rebound = admin.execute(
                "INSERT INTO paperpilot_security.service_logins SELECT s.login_role,r.oid,s.kind,s.project_id,s.revoked_at FROM restored_registry s JOIN pg_catalog.pg_roles r ON r.rolname=s.login_role"
            ).rowcount
            if rebound != count:
                raise RuntimeError("A backed-up identity is missing; restore refused")
            admin.execute("DELETE FROM paperpilot_auth.sessions")
        if database_snapshot(config) != manifest["snapshot"]:
            raise RuntimeError("Restored database differs from the complete source snapshot")
        from app.accounts import PasswordSessions
        from app.data.database import PostgresDatabase
        from app.passwords import verify_password
        from app.security.audit import verify_writer
        from app.security.executor import verify_reader

        database = PostgresDatabase(config)
        try:
            database.migrate()  # Validate all A migration checksums; does not reseed or overwrite.
            assert database.ready()
        finally:
            database.close()
        deployment = json.loads((directory / "deployment.json").read_text(encoding="utf-8"))
        environment = deployment["environment"]
        auth = PasswordSessions(
            replace(
                config,
                user=environment["PAPERPILOT_AUTH_USER"],
                password=(directory / "secrets/auth").read_text().strip(),
            )
        )
        auth.close()
        security = json.loads((directory / "security.json").read_text(encoding="utf-8"))
        with psycopg.connect(
            **replace(
                config,
                user=security["audit"]["user"],
                password=(directory / "secrets/audit").read_text().strip(),
            ).conninfo()
        ) as writer:
            verify_writer(writer, security["audit"]["user"])
        for reader in security["readers"]:
            source_path = Path(environment[reader["password_env"] + "_FILE"])
            password = (directory / "secrets" / source_path.name).read_text().strip()
            with psycopg.connect(
                **replace(config, user=reader["user"], password=password).conninfo(), autocommit=True
            ) as connection:
                verify_reader(connection, reader["user"])
                assert connection.execute(
                    "SELECT NOT EXISTS(SELECT 1 FROM paperpilot_security.paper_statistics WHERE project_id<>%s)",
                    (UUID(reader["project_id"]),),
                ).fetchone()[0]
                try:
                    connection.execute("SELECT * FROM public.chunks")
                except psycopg.errors.InsufficientPrivilege:
                    pass
                else:
                    raise RuntimeError("Restored reader has unsafe base-table access")
        with psycopg.connect(**config.conninfo()) as admin:
            encoded = admin.execute(
                "SELECT password_hash FROM paperpilot_auth.accounts WHERE user_id=%s",
                (UUID(deployment["owner_id"]),),
            ).fetchone()[0]
            assert verify_password((directory / "secrets/owner-password").read_text().strip(), encoded)
            assert admin.execute("SELECT count(*) FROM paperpilot_auth.sessions").fetchone()[0] == 0
        report = {
            "backup": directory.name,
            "confirmed_at": datetime.now(UTC).isoformat(),
            "database_tables": len(manifest["snapshot"]),
            "files_verified": len(manifest["files"]),
            "reader_isolation": True,
            "audit_identity": True,
            "password_and_session_reset": True,
            "source_database_modified": False,
            "external_model_restored": False,
        }
        write_json(STATE / "last-restore-drill.json", report)
        print("Isolated restore drill passed:", json.dumps(report))
    finally:
        if created:
            # Only the exact UUID container created above; never delete the persistent deployment.
            label = run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    '{{index .Config.Labels "paperpilot.disposable-restore"}}',
                    identifier,
                ],
                text=True,
            ).stdout.strip()
            if label != "true":
                raise RuntimeError("Restore container ownership check failed; cleanup refused")
            run(["docker", "rm", "-f", "-v", identifier])


def alert_reasons(metrics):
    alerts = []
    if not metrics.get("web_ready"):
        alerts.append("WEB_UNAVAILABLE")
    if not metrics.get("database_ready"):
        alerts.append("DATABASE_UNAVAILABLE")
    if metrics.get("dead_letters", 0):
        alerts.append("OUTBOX_DEAD_LETTERS")
    if metrics.get("expired_tasks", 0) or metrics.get("expired_deliveries", 0):
        alerts.append("EXPIRED_WORK_LEASE")
    if metrics.get("backup_age_hours") is None or metrics["backup_age_hours"] > 24:
        alerts.append("BACKUP_STALE")
    if metrics.get("disk_free_bytes", 0) < 5 * 1024**3:
        alerts.append("LOW_DISK_SPACE")
    if metrics.get("certificate_days_remaining", 0) < 14:
        alerts.append("TLS_CERTIFICATE_EXPIRING")
    return alerts


def monitor():
    import psycopg
    import ssl

    metrics = {
        "web_ready": running(),
        "database_ready": False,
        "disk_free_bytes": shutil.disk_usage(STATE).free,
        "backup_age_hours": None,
    }
    try:
        from app.data.database import PostgresDatabase

        database = PostgresDatabase(admin_config())
        try:
            metrics["database_ready"] = database.ready()
        finally:
            database.close()
        with psycopg.connect(**admin_config().conninfo()) as admin:
            metrics["dead_letters"] = admin.execute(
                "SELECT count(*) FROM public.outbox_events WHERE dead_lettered_at IS NOT NULL"
            ).fetchone()[0]
            metrics["expired_tasks"] = admin.execute(
                "SELECT count(*) FROM public.task_attempts WHERE status='processing' AND lease_expires_at<now()-interval '1 minute'"
            ).fetchone()[0]
            metrics["expired_deliveries"] = admin.execute(
                "SELECT count(*) FROM public.outbox_events WHERE published_at IS NULL AND dead_lettered_at IS NULL AND delivery_lease_until<now()-interval '1 minute'"
            ).fetchone()[0]
    except Exception:
        metrics["database_ready"] = False
    valid = []
    for directory in (STATE / "backups").iterdir():
        if directory.is_dir():
            try:
                valid.append(datetime.fromisoformat(verify_backup(directory)["created_at"]))
            except Exception:
                pass
    if valid:
        metrics["backup_age_hours"] = (datetime.now(UTC) - max(valid)).total_seconds() / 3600
    certificate = ssl._ssl._test_decode_cert(str(STATE / "tls/localhost.crt"))
    metrics["certificate_days_remaining"] = (
        ssl.cert_time_to_seconds(certificate["notAfter"]) - time.time()
    ) / 86400
    alerts = alert_reasons(metrics)
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "alerts": alerts,
        "metrics": metrics,
        "delivery": "local status file and process exit code only",
    }
    write_json(STATE / "operations-status.json", report)
    print(json.dumps(report))
    return 1 if alerts else 0


def prune_auth_metadata():
    import psycopg

    with psycopg.connect(**admin_config().conninfo()) as admin:
        sessions = admin.execute(
            "DELETE FROM paperpilot_auth.sessions WHERE expires_at<now() OR last_seen_at<now()-interval '30 minutes'"
        ).rowcount
        throttle = admin.execute(
            "DELETE FROM paperpilot_auth.throttle WHERE window_started_at<now()-interval '24 hours'"
        ).rowcount
    print(
        json.dumps(
            {
                "expired_sessions_deleted": sessions,
                "old_throttle_keys_deleted": throttle,
                "business_audit_outbox_idempotency_deleted": 0,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["backup", "restore-drill", "monitor", "prune-auth"])
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    try:
        with operator_lock():
            return dispatch(args)
    except RuntimeError:
        print("Another deployment operation is active; no action performed.", file=sys.stderr)
        return 1


def dispatch(args):
    try:
        configuration()
        if args.command == "backup":
            backup()
        elif args.command == "restore-drill" and args.backup:
            restore_drill(args.backup.resolve(strict=True))
        elif args.command == "monitor":
            return monitor()
        elif args.command == "prune-auth":
            prune_auth_metadata()
        else:
            raise ValueError("An explicit backup directory is required")
    except Exception as error:
        print(
            f"Local operation not confirmed ({type(error).__name__}); no source database restore or secret output performed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
