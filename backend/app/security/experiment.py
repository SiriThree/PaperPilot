"""Opt-in local experiment supervisor; fixed child command and owned disposable containers only."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

from app.errors import AppError
from app.schemas import SecurityExperimentState

ROOT = Path(__file__).resolve().parents[3]
LABEL = "paperpilot.security-experiment-run"
PHASES = ("no_gateway", "gateway_only", "gateway_and_database")
FAILURES = {
    "DOCKER_UNAVAILABLE",
    "DOCKER_IMAGE_MISSING",
    "DOCKER_CONTAINER_FAILED",
    "EXPERIMENT_FAILED",
    "EXPERIMENT_TIMEOUT",
    "EXPERIMENT_INVALID_RESULT",
}


def child_environment(run_id):
    # Business, authentication and model credentials never enter the experiment child.
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
        "DOCKER_CERT_PATH",
        "DOCKER_TLS_VERIFY",
    }
    return {k: v for k, v in os.environ.items() if k.upper() in allowed} | {
        "PAPERPILOT_SECURITY_CURRENT_SOURCE": "1",
        "PAPERPILOT_EXPERIMENT_RUN_ID": str(run_id),
        "PYTHONIOENCODING": "utf-8",
    }


def cleanup_containers(run_id):
    result = subprocess.run(
        ["docker", "ps", "-aq", "--no-trunc", "--filter", f"label={LABEL}={run_id}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    containers = result.stdout.split()
    if any(not re.fullmatch(r"[a-f0-9]{64}", value) for value in containers):
        raise RuntimeError("Invalid owned container identity")
    if containers:
        subprocess.run(["docker", "rm", "--force", *containers], capture_output=True, check=True, timeout=30)


class LocalSecurityExperiments:
    def __init__(self):
        self.state = SecurityExperimentState(enabled=True)
        self.owner = None
        self.task = None

    def current(self, ctx):
        if self.owner != (ctx.project_id, ctx.user_id):
            return SecurityExperimentState(enabled=True, busy=self.state.busy)
        return self.state

    def start(self, ctx):
        if self.state.busy:
            if self.owner == (ctx.project_id, ctx.user_id):
                return self.state
            raise AppError(409, "EXPERIMENT_BUSY", "另一个隔离实验正在运行")
        self.owner = (ctx.project_id, ctx.user_id)
        self.state = SecurityExperimentState(
            enabled=True,
            busy=True,
            run_id=uuid4(),
            status="running",
            phase="starting",
            started_at=datetime.now(UTC),
        )
        self.task = asyncio.create_task(self._run(self.state))
        return self.state

    async def _run(self, state):
        process = None
        completed = False
        output = ROOT / ".runtime/security-experiments" / str(state.run_id) / "result.json"
        try:
            async with asyncio.timeout(900):
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-X",
                    "utf8",
                    str(ROOT / "scripts/evaluate_security.py"),
                    "--demo",
                    "--output",
                    str(output),
                    cwd=ROOT,
                    env=child_environment(state.run_id),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                failure = "EXPERIMENT_FAILED"
                while line := await process.stdout.readline():
                    text = line.decode("utf-8", errors="replace").strip()
                    for phase in PHASES:
                        if text.startswith(phase + ": running "):
                            state.phase = phase
                    if text.startswith("EXPERIMENT_ERROR:") and text.split(":", 1)[1] in FAILURES:
                        failure = text.split(":", 1)[1]
                code = await process.wait()
                if code in (0, 1) and output.is_file() and output.stat().st_size <= 5 * 1024 * 1024:
                    report = json.loads(output.read_text(encoding="utf-8"))
                    if (
                        report.get("scope") != "disposable_postgres_security_experiment"
                        or report.get("run_id") != str(state.run_id)
                        or report.get("schema_version") != 1
                        or len(report.get("configurations", [])) != 3
                        or datetime.fromisoformat(report["generated_at"]) < state.started_at
                    ):
                        raise ValueError("Invalid experiment result")
                    state.report = report
                    completed = True
                else:
                    state.error = failure
                    state.status = "failed"
        except TimeoutError:
            completed = False
            state.status, state.error = "failed", "EXPERIMENT_TIMEOUT"
        except asyncio.CancelledError:
            completed = False
            state.status, state.error = "failed", "EXPERIMENT_CANCELLED"
        except Exception:
            completed = False
            state.status, state.error = (
                "failed",
                "EXPERIMENT_INVALID_RESULT" if output.exists() else "EXPERIMENT_FAILED",
            )
        finally:
            if process is not None and process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
            try:
                await asyncio.to_thread(cleanup_containers, state.run_id)
            except Exception:
                if completed:
                    state.status, state.error = "failed", "EXPERIMENT_CLEANUP_FAILED"
                    completed = False
            if completed:
                state.status = "succeeded"
            if state.status != "succeeded":
                state.report = None
            state.busy, state.phase, state.finished_at = False, "finished", datetime.now(UTC)

    async def close(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            if self.state.busy:
                self.state.status, self.state.error = "failed", "EXPERIMENT_CANCELLED"
                self.state.busy, self.state.phase = False, "finished"
                self.state.finished_at = datetime.now(UTC)
