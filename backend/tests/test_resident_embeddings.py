"""Controlled wire worker tests; not a real BGE vector comparison."""

import asyncio
import json
from uuid import uuid4

import pytest

from app.ai.resident_embeddings import ResidentEmbeddingWorker
from app.errors import AppError


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Input:
    def __init__(self, process):
        self.process = process

    def write(self, line):
        request = json.loads(line)
        self.process.requests.append(request)
        if not self.process.hang:
            self.process.stdout.feed_data(
                json.dumps({"id": request["id"], "vector": self.process.vector}).encode() + b"\n"
            )

    async def drain(self):
        pass


class Process:
    def __init__(self, *, hang=False, ready=True):
        self.returncode = None
        self.hang = hang
        self.vector = [1.0, 0.0, 0.0]
        self.requests = []
        self.killed = self.waited = False
        self.stdout = asyncio.StreamReader()
        self.stdin = Input(self)
        if ready:
            self.stdout.feed_data(b'{"ready":true}\n')

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.stdout.feed_eof()

    async def wait(self):
        self.waited = True
        return self.returncode


@pytest.mark.anyio
async def test_query_worker_is_reused_and_closed():
    spawned = []

    async def spawn():
        p = Process()
        spawned.append(p)
        return p

    worker = ResidentEmbeddingWorker(spawn, 1, 2)
    await worker.start()
    assert await worker.query("first") == [1.0, 0.0, 0.0]
    assert await worker.query("second") == [1.0, 0.0, 0.0]
    assert len(spawned) == 1 and len(spawned[0].requests) == 2
    assert spawned[0].requests[0]["id"] != spawned[0].requests[1]["id"]
    await worker.aclose()
    assert spawned[0].killed and spawned[0].waited
    with pytest.raises(AppError):
        await worker.query("after close")


@pytest.mark.anyio
async def test_full_queue_cancel_waiter_does_not_kill_running_request():
    p = Process(hang=True)

    async def spawn():
        return p

    worker = ResidentEmbeddingWorker(spawn, 5, 1)
    active = asyncio.create_task(worker.query("active"))
    while not p.requests:
        await asyncio.sleep(0)
    queued = asyncio.create_task(worker.query("queued"))
    await asyncio.sleep(0)
    with pytest.raises(AppError) as error:
        await worker.query("overflow")
    assert error.value.code == "AI_EMBEDDING_BUSY"
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert not p.killed
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    assert p.killed and p.waited and worker._pending == 0


@pytest.mark.anyio
async def test_request_timeout_reaps_and_next_request_restarts():
    processes = [Process(hang=True), Process()]

    async def spawn():
        return processes.pop(0)

    worker = ResidentEmbeddingWorker(spawn, 0.02, 1)
    bad = processes[0]
    with pytest.raises(AppError) as error:
        await worker.query("timeout")
    assert error.value.code == "AI_TIMEOUT" and bad.killed and bad.waited
    assert await worker.query("recovered") == [1.0, 0.0, 0.0]
    await worker.aclose()


@pytest.mark.anyio
async def test_startup_timeout_reaps_partial_process():
    p = Process(ready=False)

    async def spawn():
        return p

    worker = ResidentEmbeddingWorker(spawn, 0.02, 1)
    with pytest.raises(TimeoutError):
        await worker.start()
    assert p.killed and p.waited


@pytest.mark.anyio
async def test_crash_restarts_and_protocol_ids_are_checked():
    p, replacement = Process(), Process()
    processes = [p, replacement]

    async def spawn():
        return processes.pop(0)

    worker = ResidentEmbeddingWorker(spawn, 1, 1)
    await worker.start()
    p.kill()
    assert await worker.query("restart") == replacement.vector
    replacement.stdout.feed_data(json.dumps({"id": uuid4().hex, "vector": [1.0, 0.0, 0.0]}).encode() + b"\n")
    with pytest.raises(AppError) as error:
        await worker.query("wrong protocol id")
    assert error.value.code == "AI_EMBEDDING_UNAVAILABLE" and replacement.killed and replacement.waited


@pytest.mark.anyio
async def test_bad_vector_is_checked_while_request_owns_worker():
    p = Process()

    def validate(vector):
        if vector != [1.0, 0.0, 0.0]:
            raise AppError(502, "AI_INVALID_RESPONSE", "invalid fixture vector")
        return vector

    async def spawn():
        return p

    worker = ResidentEmbeddingWorker(spawn, 1, 1, validate)
    p.vector = [float("nan")]
    with pytest.raises(AppError) as error:
        await worker.query("bad vector")
    assert error.value.code == "AI_INVALID_RESPONSE" and p.killed and p.waited
