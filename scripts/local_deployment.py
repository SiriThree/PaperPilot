"""Owned loopback deployment: persistent PostgreSQL, private secret files and HTTPS web app."""

import argparse
import csv
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".runtime/local-deployment"
CONFIG = STATE / "deployment.json"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "compose.local.yaml")]
sys.path.insert(0, str(ROOT / "backend"))


def run(command, **kwargs):
    return subprocess.run(command, check=True, capture_output=True, **kwargs)


@contextmanager
def file_lock(path, timeout=60):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Another owned deployment operation is running") from None
                time.sleep(0.5)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def operator_lock():
    return file_lock(ROOT / ".runtime/local-operations.lock")


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        row = next(csv.reader(run(["whoami", "/user", "/fo", "csv", "/nh"], text=True).stdout.splitlines()))
        run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{row[-1]}:(OI)(CI)F"])
    else:
        path.chmod(0o700)


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def configuration():
    value = json.loads(CONFIG.read_text(encoding="utf-8"))
    if value.get("format") != 1 or value.get("root") != str(ROOT):
        raise ValueError("Unknown deployment; explicit operator migration required")
    return value


def admin_config(port=54327):
    from app.data.database import ConnectionConfig

    return ConnectionConfig(
        "127.0.0.1", port, "postgres", (STATE / "secrets/postgres").read_text().strip(), "paperpilot_local"
    )


def runtime_environment(config):
    environment = dict(os.environ)
    # No inherited demo/administrator/model setting may silently alter this deployment.
    for name in list(environment):
        if name.startswith("PAPERPILOT_"):
            del environment[name]
    environment.update(config["environment"])
    if config.get("ai_config"):
        model = json.loads(Path(config["ai_config"]).read_text(encoding="utf-8-sig"))
        if not isinstance(model, dict) or any(
            not key.startswith("PAPERPILOT_AI_") or not isinstance(value, str) for key, value in model.items()
        ):
            raise ValueError("Model configuration must contain only explicit PAPERPILOT_AI_* strings")
        environment.update(model)
    return environment


def health():
    import httpx

    context = ssl.create_default_context(cafile=str(STATE / "tls/localhost.crt"))
    with httpx.Client(verify=context, trust_env=False, timeout=5) as client:
        response = client.get("https://localhost:8443/api/v1/health")
        response.raise_for_status()
        return response.json()


def container():
    result = run([*COMPOSE, "ps", "-q", "postgres"], text=True).stdout.strip()
    if not result or "\n" in result:
        raise RuntimeError("Owned PostgreSQL container is unavailable")
    return result


def ensure_docker():
    try:
        run(["docker", "info", "--format", "{{.ServerVersion}}"])
        return
    except subprocess.CalledProcessError:
        desktop = Path("C:/Program Files/Docker/Docker/Docker Desktop.exe")
        if os.name != "nt" or not desktop.is_file():
            raise RuntimeError("Start the authorized Docker engine first") from None
        run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Start-Process -FilePath 'C:/Program Files/Docker/Docker/Docker Desktop.exe' -WindowStyle Hidden",
            ]
        )
    for _ in range(60):
        try:
            run(["docker", "info", "--format", "{{.ServerVersion}}"])
            return
        except subprocess.CalledProcessError:
            time.sleep(2)
    raise RuntimeError("Docker Desktop did not become ready")


def init():
    if STATE.exists():
        raise ValueError("Deployment directory already exists; initialization never adopts or overwrites it")
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise ValueError("Run npm --prefix frontend run build first")
    ensure_docker()
    for port in (54327, 8443):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    existing = run([*COMPOSE, "ps", "-aq"], text=True).stdout.strip()
    volumes = run(
        ["docker", "volume", "ls", "-q", "--filter", "name=^paperpilot-local_postgres$"], text=True
    ).stdout.strip()
    if existing or volumes:
        raise ValueError("Existing Docker resources refused; inspect them before initializing")
    openssl = shutil.which("openssl")
    if not openssl:
        raise ValueError("An installed OpenSSL command is required for the local certificate")
    private_directory(STATE)
    for name in ("secrets", "tls", "uploads", "logs", "backups"):
        (STATE / name).mkdir()
    for name in ("postgres", "business", "audit", "reader", "auth", "owner-password"):
        (STATE / "secrets" / name).write_text(secrets.token_urlsafe(32), encoding="utf-8")
    run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:3072",
            "-nodes",
            "-days",
            "365",
            "-keyout",
            str(STATE / "tls/localhost.key"),
            "-out",
            str(STATE / "tls/localhost.crt"),
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ]
    )
    run([*COMPOSE, "up", "-d", "--wait", "--wait-timeout", "120"])
    from app.accounts import AccountAdmin
    from app.data.database import PostgresDatabase
    from app.data.settings import DataSettings
    from app.schemas import ProjectCreate
    from app.security.provision import create_login, install, install_password_auth
    import psycopg

    database = PostgresDatabase(admin_config())
    try:
        database.migrate()
        with psycopg.connect(**admin_config().conninfo(), autocommit=True) as admin:
            install(admin)
            install_password_auth(admin)
        accounts = AccountAdmin(database, DataSettings())
        owner_id = accounts.create_account(
            "owner", "本机项目负责人", (STATE / "secrets/owner-password").read_text()
        )
        project = accounts.create_project(
            owner_id,
            ProjectCreate(name="本机持久项目", description="真实 PostgreSQL 与安全网关；AI 状态见运行说明"),
            uuid4(),
        )
        roles = {
            kind: "pp_" + kind + "_local_" + uuid4().hex[:12]
            for kind in ("business", "audit", "reader", "auth")
        }
        with psycopg.connect(**admin_config().conninfo(), autocommit=True) as admin:
            for kind, role in roles.items():
                create_login(
                    admin,
                    role,
                    (STATE / "secrets" / kind).read_text(),
                    project_id=project.project_id if kind == "reader" else None,
                    business=kind == "business",
                    auth=kind == "auth",
                )
        security = {
            "database": {"host": "127.0.0.1", "port": 54327, "dbname": "paperpilot_local"},
            "audit": {"user": roles["audit"], "password_env": "PAPERPILOT_LOCAL_AUDIT"},
            "readers": [
                {
                    "user": roles["reader"],
                    "password_env": "PAPERPILOT_LOCAL_READER",
                    "project_id": str(project.project_id),
                }
            ],
        }
        write_json(STATE / "security.json", security)
        environment = {
            "PAPERPILOT_ENV": "development",
            "PAPERPILOT_A_MODE": "persistent",
            "PAPERPILOT_C_MODE": "mock",
            "PAPERPILOT_D_MODE": "real",
            "PAPERPILOT_AUTH_MODE": "password",
            "PAPERPILOT_ALLOWED_ORIGINS": "https://localhost:8443,https://127.0.0.1:8443",
            "PAPERPILOT_DATA_HOST": "127.0.0.1",
            "PAPERPILOT_DATA_PORT": "54327",
            "PAPERPILOT_DATA_DBNAME": "paperpilot_local",
            "PAPERPILOT_DATA_USER": roles["business"],
            "PAPERPILOT_AUTH_USER": roles["auth"],
            "PAPERPILOT_SECURITY_CONFIG": str(STATE / "security.json"),
            "PAPERPILOT_STORAGE_DIR": str(STATE / "uploads"),
            "PAPERPILOT_FRONTEND_DIR": str(ROOT / "frontend/dist"),
        }
        for reference, filename in (
            ("PAPERPILOT_DATA_PASSWORD", "business"),
            ("PAPERPILOT_LOCAL_AUDIT", "audit"),
            ("PAPERPILOT_LOCAL_READER", "reader"),
            ("PAPERPILOT_AUTH_PASSWORD", "auth"),
        ):
            environment[reference + "_FILE"] = str(STATE / "secrets" / filename)
        write_json(
            CONFIG,
            {
                "format": 1,
                "root": str(ROOT),
                "created_at": datetime.now(UTC).isoformat(),
                "owner_id": str(owner_id),
                "project_id": str(project.project_id),
                "environment": environment,
                "ai_config": None,
            },
        )
    finally:
        database.close()
    print(
        "Initialized local persistent database and personal owner account; password stays in secrets/owner-password."
    )


def running():
    try:
        health()
        return True
    except Exception:
        return False


def start():
    configuration()
    if running():
        print("Local HTTPS deployment is already running.")
        return
    ensure_docker()
    run([*COMPOSE, "up", "-d", "--wait", "--wait-timeout", "120"])
    (STATE / "stop").unlink(missing_ok=True)
    with (STATE / "logs/server.log").open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "serve"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
    for _ in range(45):
        if running():
            print(
                "Local HTTPS deployment ready: https://localhost:8443 (AI mode: "
                + configuration()["environment"]["PAPERPILOT_C_MODE"]
                + ")."
            )
            return
        if process.poll() is not None:
            raise RuntimeError("Web startup refused; inspect logs/server.log")
        time.sleep(1)
    # A slow model must not leave an unconfirmed process starting behind the operator.
    stop()
    raise RuntimeError("Web readiness timeout")


def stop():
    (STATE / "stop").touch()
    for _ in range(40):
        if not (STATE / "server.json").exists():
            return
        try:
            with file_lock(STATE / "server.lock", timeout=0):
                (STATE / "server.json").unlink(missing_ok=True)
                return
        except RuntimeError:
            pass
        time.sleep(1)
    raise RuntimeError("Web stop unconfirmed; database and process were not forcibly terminated")


def serve_worker():
    import uvicorn

    config = configuration()
    environment = runtime_environment(config)
    os.environ.clear()
    os.environ.update(environment)
    server = uvicorn.Server(
        uvicorn.Config(
            "app.deployment:create_app",
            factory=True,
            host="127.0.0.1",
            port=8443,
            ssl_keyfile=str(STATE / "tls/localhost.key"),
            ssl_certfile=str(STATE / "tls/localhost.crt"),
            proxy_headers=False,
            access_log=False,
            log_level="warning",
            timeout_graceful_shutdown=25,
        )
    )
    write_json(STATE / "server.json", {"pid": os.getpid(), "started_at": datetime.now(UTC).isoformat()})

    def watch():
        while not server.should_exit:
            if (STATE / "stop").exists():
                server.should_exit = True
                return
            time.sleep(0.5)

    threading.Thread(target=watch, daemon=True).start()
    try:
        server.run()
    finally:
        (STATE / "server.json").unlink(missing_ok=True)


def serve():
    with file_lock(STATE / "server.lock", timeout=0):
        serve_worker()


def add_reader(project_id):
    from app.security.provision import create_login
    import psycopg

    config = configuration()
    security_path = STATE / "security.json"
    security = json.loads(security_path.read_text(encoding="utf-8"))
    if any(reader["project_id"] == str(project_id) for reader in security["readers"]):
        raise ValueError("Project already has an explicit reader")
    suffix = uuid4().hex[:16]
    path = STATE / "secrets" / ("reader-" + suffix)
    path.write_text(secrets.token_urlsafe(32))
    reference = "PAPERPILOT_READER_" + suffix.upper()
    with psycopg.connect(**admin_config().conninfo(), autocommit=True) as admin:
        create_login(admin, "pp_reader_" + suffix, path.read_text(), project_id=project_id)
    security["readers"].append(
        {"user": "pp_reader_" + suffix, "password_env": reference, "project_id": str(project_id)}
    )
    config["environment"][reference + "_FILE"] = str(path)
    write_json(security_path, security)
    write_json(CONFIG, config)
    print("Reader provisioned; restart the web app to load its explicit binding.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["init", "start", "stop", "restart", "serve", "status", "reader-add", "model-configure"],
    )
    parser.add_argument("--project", type=UUID)
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    if args.command == "serve":
        return dispatch(args)
    try:
        with operator_lock():
            return dispatch(args)
    except RuntimeError:
        print("Another deployment operation is active; no action performed.", file=sys.stderr)
        return 1


def dispatch(args):
    try:
        if args.command == "init":
            init()
        elif args.command == "serve":
            serve()
        elif args.command == "start":
            start()
        elif args.command == "stop":
            stop()
        elif args.command == "restart":
            stop()
            start()
        elif args.command == "reader-add" and args.project:
            add_reader(args.project)
        elif args.command == "model-configure" and args.file and args.file.is_absolute():
            from app.ai.settings import AISettings

            config = configuration()
            config["ai_config"] = str(args.file.resolve(strict=True))
            environment = runtime_environment(config)
            previous = dict(os.environ)
            try:
                os.environ.clear()
                os.environ.update(environment)
                AISettings.from_env()
            finally:
                os.environ.clear()
                os.environ.update(previous)
            config["environment"]["PAPERPILOT_C_MODE"] = "real"
            write_json(CONFIG, config)
            print(
                "Explicit real model configuration installed; restart and run live-model acceptance before formal use."
            )
        elif args.command == "status":
            print(json.dumps(health(), ensure_ascii=False))
        else:
            raise ValueError("Required project/configuration argument missing")
    except Exception as error:
        print(
            f"Local deployment operation not confirmed ({type(error).__name__}); inspect owned state/logs. No secrets displayed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
