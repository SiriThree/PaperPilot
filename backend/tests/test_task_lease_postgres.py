"""Lease fencing on an explicitly isolated PostgreSQL instance."""

import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

from app.adapters.ports import Context
from app.data.adapter import PostgresData
from app.data.database import PostgresDatabase
from app.data.settings import DataSettings
from app.errors import AppError
from app.schemas import ProjectCreate
from data_contracts.support import import_paper, processing_result
from test_phase5a_persistent import database  # noqa: F401

pytestmark = pytest.mark.real_database


def test_expired_attempt_cannot_commit_before_or_after_reclaim(database):  # noqa: F811
    config, _ = database
    pool = PostgresDatabase(config)
    try:
        data = PostgresData(pool, DataSettings())
        owner = data.user_by_name("alice")
        project = data.create_project(owner.user_id, ProjectCreate(name="Lease fence"), uuid4())
        ctx = Context(owner.user_id, project.project_id, uuid4())
        paper, task = import_paper(data, ctx)
        old = data.claim_job()
        with pool.transaction(write=True) as conn:
            conn.execute(
                "UPDATE task_attempts SET claimed_at='1999-01-01', lease_expires_at='2000-01-01'"
                " WHERE task_id=%s AND attempt_no=%s",
                (task.task_id, old.task.attempts),
            )
        for operation in (
            lambda: data.complete_job(old, processing_result(paper.paper_id)),
            lambda: data.fail_job(old, "TEST_FAILURE", "safe test error"),
        ):
            with pytest.raises(AppError) as error:
                operation()
            assert error.value.code == "TASK_STATE_CONFLICT"
        assert data.task(ctx, task.task_id).status == "processing"
        new = data.claim_job()
        assert new.task.attempts == old.task.attempts + 1
        with pytest.raises(AppError) as error:
            data.complete_job(old, processing_result(paper.paper_id))
        assert error.value.code == "TASK_STATE_CONFLICT"
        data.complete_job(new, processing_result(paper.paper_id))
        assert data.task(ctx, task.task_id).status == "succeeded"
    finally:
        pool.close()


def test_worker_process_crash_then_new_worker_recovers_expired_task(database):  # noqa: F811
    config, _ = database
    pool = PostgresDatabase(config)
    processes = []
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "backend")
    worker_code = """
import asyncio, os
from app.adapters.ports import Adapters
from app.adapters.mock_ai import MockAI
from app.adapters.mock_security import MockSecurity
from app.data.binding import compose_data_binding
from app.data.database import ConnectionConfig
from app.data.settings import DataSettings
from app.services import ResearchService
binding = compose_data_binding(ConnectionConfig.from_env(), DataSettings())
ai = MockAI(binding.data)
if os.environ.get('PAPERPILOT_TEST_STALL') == '1':
    async def stalled(*args):
        await asyncio.sleep(300)
    ai.process = stalled
service = ResearchService(Adapters(binding.data, ai, MockSecurity(binding.data)))
try:
    asyncio.run(service.worker())
finally:
    binding.close()
"""

    def start_worker(stall):
        worker_env = {**env, "PAPERPILOT_TEST_STALL": "1" if stall else "0"}
        process = subprocess.Popen(
            [sys.executable, "-c", worker_code],
            cwd=root,
            env=worker_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        processes.append(process)
        return process

    def wait_status(data, ctx, task_id, expected, process):
        for _ in range(200):
            if process.poll() is not None:
                pytest.fail("Isolated worker exited before reaching expected state")
            state = data.task(ctx, task_id)
            if state.status == expected:
                return state
            time.sleep(0.1)
        pytest.fail("Isolated worker did not reach expected state")

    try:
        data = PostgresData(pool, DataSettings())
        owner = data.user_by_name("alice")
        project = data.create_project(owner.user_id, ProjectCreate(name="Worker restart"), uuid4())
        ctx = Context(owner.user_id, project.project_id, uuid4())
        paper, task = import_paper(data, ctx)
        first = start_worker(True)
        assert wait_status(data, ctx, task.task_id, "processing", first).attempts == 1
        first.terminate()  # Only the process created by this test.
        first.wait(timeout=10)
        with pool.transaction(write=True) as conn:
            conn.execute(
                "UPDATE task_attempts SET claimed_at='1999-01-01', lease_expires_at='2000-01-01'"
                " WHERE task_id=%s AND attempt_no=1",
                (task.task_id,),
            )
        second = start_worker(False)
        state = wait_status(data, ctx, task.task_id, "succeeded", second)
        assert state.attempts == 2
        assert data.paper(ctx, paper.paper_id).status == "searchable"
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        pool.close()
