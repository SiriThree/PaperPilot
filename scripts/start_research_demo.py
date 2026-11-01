"""Start an operator-prepared local PostgreSQL/SJTU/BGE development workspace.

No migrations, password resets or downloads happen here. Configuration and secret
files remain under .runtime; only the three child processes are stopped on exit.
"""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def ready(url, process):
    for _ in range(150):
        if process.poll() is not None:
            raise RuntimeError("Owned service stopped; inspect its local log")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("Owned service did not become ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".runtime/real-demo")
    parser.add_argument("--model-manifest", type=Path, default=ROOT / ".runtime/ai-model.json")
    parser.add_argument(
        "--provider-config", type=Path, required=True, help="Trusted local provider configuration"
    )
    parser.add_argument("--provider", default="sjtu")
    parser.add_argument("--chat-model", default="deepseek-chat")
    parser.add_argument("--api-port", type=int, default=18801)
    parser.add_argument("--web-port", type=int, default=15174)
    parser.add_argument(
        "--api-workers", type=int, default=1, help="Temporary import workers; use one for UI demos"
    )
    args = parser.parse_args()
    if not 1 <= args.api_workers <= 8:
        parser.error("Use between one and eight API workers")
    state = args.state_dir.resolve(strict=True)
    database = json.loads((state / "database.json").read_text())
    model = json.loads(args.model_manifest.read_text())
    provider_config = json.loads(args.provider_config.read_text())
    # OpenClaw's existing provider registry or a standalone {baseUrl, apiKey} file.
    provider = (
        provider_config["models"]["providers"][args.provider]
        if "models" in provider_config
        else provider_config
    )
    for port in [args.api_port, args.web_port]:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    api_url, web_url = f"http://127.0.0.1:{args.api_port}", f"http://127.0.0.1:{args.web_port}"
    environment = dict(
        os.environ,
        PAPERPILOT_ENV="development",
        PAPERPILOT_AUTH_MODE="credentials",
        PAPERPILOT_AUTH_DATABASE="shared",
        PAPERPILOT_A_MODE="persistent",
        PAPERPILOT_C_MODE="real",
        PAPERPILOT_D_MODE="real",
        PAPERPILOT_SECURITY_EXECUTION="disabled",
        PAPERPILOT_AUDIT_PREFLIGHT="disabled",
        PAPERPILOT_DATA_HOST=database["host"],
        PAPERPILOT_DATA_PORT=str(database["port"]),
        PAPERPILOT_DATA_USER=database["user"],
        PAPERPILOT_DATA_PASSWORD="",
        PAPERPILOT_DATA_PASSWORD_FILE=database["password_file"],
        PAPERPILOT_DATA_DBNAME=database["dbname"],
        PAPERPILOT_ALLOWED_ORIGINS=f"{web_url},http://localhost:{args.web_port},{api_url}",
        PAPERPILOT_STORAGE_DIR=str(state / "uploads"),
        PAPERPILOT_AI_BASE_URL=provider["baseUrl"],
        PAPERPILOT_AI_API_KEY=provider["apiKey"],
        PAPERPILOT_AI_API_KEY_FILE="",
        PAPERPILOT_AI_CHAT_MODEL=args.chat_model,
        PAPERPILOT_AI_RESPONSE_FORMAT="json_object",
        PAPERPILOT_AI_EMBEDDING_BACKEND="fastembed",
        PAPERPILOT_AI_EMBEDDING_MODEL=model["model"],
        PAPERPILOT_AI_EMBEDDING_DIMENSIONS=str(model["dimensions"]),
        PAPERPILOT_AI_LOCAL_MODEL_DIR=model["model_dir"],
        PAPERPILOT_AI_EMBEDDING_REVISION=Path(model["model_dir"]).name,
        PAPERPILOT_AI_TIMEOUT_SECONDS="90",
        PAPERPILOT_PROXY_TARGET=api_url,
    )
    commands = [
        (
            "backend",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.dev_composition:create_app",
                "--factory",
                "--app-dir",
                "backend",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.api_port),
                "--workers",
                str(args.api_workers),
                "--no-access-log",
            ],
            ROOT,
            api_url + "/api/v1/health",
        ),
        ("outbox", [sys.executable, "-m", "app.data.deliverer"], ROOT, None),
        (
            "frontend",
            [
                "node",
                str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
                "--host",
                "127.0.0.1",
                "--port",
                str(args.web_port),
                "--strictPort",
            ],
            ROOT / "frontend",
            web_url,
        ),
    ]
    stop = state / "app.stop"
    stop.unlink(missing_ok=True)
    children, logs = [], []
    try:
        for name, command, cwd, url in commands:
            log = (state / (name + ".log")).open("a")
            logs.append(log)
            process = subprocess.Popen(
                command, cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT
            )
            children.append(process)
            if url:
                ready(url, process)
        preview = {
            "url": web_url,
            "api_url": api_url,
            "mode": "persistent-postgresql-real-sjtu-real-bge-security-policy",
            "username": database["login_username"],
            "password_file": database["login_password_file"],
            "backend_pid": children[0].pid,
            "outbox_pid": children[1].pid,
            "frontend_pid": children[2].pid,
            "stop_file": str(stop),
            "api_workers": args.api_workers,
        }
        (state / "preview.json").write_text(json.dumps(preview, indent=2) + "\n")
        print(json.dumps(preview), flush=True)
        while not stop.exists() and all(process.poll() is None for process in children):
            time.sleep(0.5)
        if not stop.exists():
            raise RuntimeError("Owned service exited; inspect local logs")
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
