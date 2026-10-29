"""Disposable A/C/D browser fixture. Local model double, generated PDF, no business database."""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]


def port():
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return candidate.getsockname()[1]


def ready(url, process):
    deadline = time.monotonic() + 30
    while process.poll() is None and time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            time.sleep(0.1)
    raise RuntimeError("Owned integration service did not become ready; inspect its local log")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state",
        type=Path,
        required=True,
        help="Nonsecret runtime state file; create <state>.stop to shut down",
    )
    args = parser.parse_args()
    args.state = args.state.resolve()
    args.state.parent.mkdir(parents=True, exist_ok=True)
    stop = args.state.with_suffix(args.state.suffix + ".stop")
    if args.state.exists() or stop.exists():
        raise RuntimeError("Select a new state filename; do not overwrite another running fixture")
    os.environ["PAPERPILOT_SECURITY_CURRENT_SOURCE"] = "1"
    import psycopg
    from app.adapters.ports import FileReference
    from app.security.provision import create_login, install
    from security_contracts.postgres_provider import postgres_workspace
    from test_security_team_integration import local_model, pdf_bytes

    with ExitStack() as resources:
        pg = resources.enter_context(postgres_workspace())
        model = resources.enter_context(local_model())
        directory = ROOT / ".runtime" / ("integration-preview-" + uuid4().hex[:12])
        directory.mkdir()
        uploads = directory / "uploads"
        uploads.mkdir()
        audit_role, audit_password = "pp_audit_" + uuid4().hex, secrets.token_urlsafe(32)
        business_role, business_password = "pp_business_" + uuid4().hex, secrets.token_urlsafe(32)
        readers, passwords = [], {}
        with psycopg.connect(**pg.config.conninfo(), autocommit=True) as admin:
            install(admin, existing_views=True)
            create_login(admin, business_role, business_password, business=True)
            create_login(admin, audit_role, audit_password)
            for index, ctx in enumerate(pg.contexts):
                role, password = "pp_reader_" + uuid4().hex, secrets.token_urlsafe(32)
                create_login(admin, role, password, project_id=ctx.project_id)
                key = f"PAPERPILOT_PREVIEW_READER_{index}_PASSWORD"
                readers.append({"project_id": str(ctx.project_id), "user": role, "password_env": key})
                passwords[key] = password
        document = {
            "database": {"host": pg.config.host, "port": pg.config.port, "dbname": pg.config.dbname},
            "audit": {"user": audit_role, "password_env": "PAPERPILOT_PREVIEW_AUDIT_PASSWORD"},
            "readers": readers,
        }
        configuration = directory / "security.json"
        configuration.write_text(json.dumps(document), encoding="utf-8")
        # The preview's research project uses only its parsed PDF, not the provider's Mock index fixtures.
        for seeded in pg.data.papers(pg.contexts[0]):
            pg.data.remove_paper(pg.contexts[0], seeded.paper_id)
        paper_id = uuid4()
        pdf = pdf_bytes("controlled fixture, not research evidence")
        (uploads / f"{paper_id}.pdf").write_bytes(pdf)
        paper, _ = pg.data.import_paper(
            pg.contexts[0],
            "受控 PDF 联调样例",
            2026,
            hashlib.sha256(pdf).hexdigest(),
            FileReference(f"{paper_id}.pdf", paper_id, pg.contexts[0].project_id),
            uuid4().hex,
        )
        api_port, web_port = port(), port()
        environment = dict(
            os.environ,
            PAPERPILOT_ENV="test",
            PAPERPILOT_AUTH_MODE="development",
            PAPERPILOT_A_MODE="persistent",
            PAPERPILOT_C_MODE="real",
            PAPERPILOT_D_MODE="real",
            PAPERPILOT_SECURITY_CONFIG=str(configuration),
            PAPERPILOT_STORAGE_DIR=str(uploads),
            PAPERPILOT_ALLOWED_ORIGINS=f"http://127.0.0.1:{web_port}",
            PAPERPILOT_DATA_HOST=pg.config.host,
            PAPERPILOT_DATA_PORT=str(pg.config.port),
            PAPERPILOT_DATA_USER=business_role,
            PAPERPILOT_DATA_PASSWORD=business_password,
            PAPERPILOT_DATA_DBNAME=pg.config.dbname,
            PAPERPILOT_PREVIEW_AUDIT_PASSWORD=audit_password,
            PAPERPILOT_AI_BASE_URL=model.url,
            PAPERPILOT_AI_API_KEY="local-test-key",
            PAPERPILOT_AI_CHAT_MODEL="test-chat",
            PAPERPILOT_AI_EMBEDDING_MODEL="test-embedding",
            PAPERPILOT_AI_EMBEDDING_REVISION="test-v1",
            PAPERPILOT_AI_EMBEDDING_DIMENSIONS="3",
            PAPERPILOT_AI_EMBEDDING_BACKEND="remote",
            PAPERPILOT_PROXY_TARGET=f"http://127.0.0.1:{api_port}",
            **passwords,
        )
        children = []

        def shutdown():
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=15)

        resources.callback(shutdown)
        backend_log = resources.enter_context((directory / "backend.log").open("w", encoding="utf-8"))
        backend = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts/run_security.py"), "--port", str(api_port)],
            cwd=ROOT,
            env=environment,
            stdout=backend_log,
            stderr=subprocess.STDOUT,
        )
        children.append(backend)
        ready(f"http://127.0.0.1:{api_port}/api/v1/health", backend)
        frontend_log = resources.enter_context((directory / "frontend.log").open("w", encoding="utf-8"))
        frontend = subprocess.Popen(
            [
                "node",
                str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
                "--host",
                "127.0.0.1",
                "--port",
                str(web_port),
                "--strictPort",
            ],
            cwd=ROOT / "frontend",
            env=environment,
            stdout=frontend_log,
            stderr=subprocess.STDOUT,
        )
        children.append(frontend)
        ready(f"http://127.0.0.1:{web_port}/", frontend)
        state = {
            "url": f"http://127.0.0.1:{web_port}",
            "api_url": f"http://127.0.0.1:{api_port}",
            "project_id": str(pg.contexts[0].project_id),
            "paper_id": str(paper.paper_id),
            "stop_file": str(stop),
            "scope": "disposable_postgres_nonowner_business_real_adapters_local_model_double",
            "logs": str(directory),
        }
        args.state.write_text(json.dumps(state), encoding="utf-8")
        print(json.dumps(state), flush=True)
        while not stop.exists():
            if any(child.poll() is not None for child in children):
                raise RuntimeError("An owned preview service stopped")
            time.sleep(0.25)


if __name__ == "__main__":
    main()
