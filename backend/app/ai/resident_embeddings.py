"""Bounded query-only worker. Batch document embedding retains its separate process."""

import asyncio
import json
from uuid import uuid4

from app.errors import AppError


def unavailable():
    return AppError(503, "AI_EMBEDDING_UNAVAILABLE", "本地查询嵌入服务暂不可用")


class ResidentEmbeddingWorker:
    def __init__(self, spawn, timeout, queue_size, normalize=None):
        self.spawn = spawn
        self.timeout = timeout
        self.queue_size = queue_size
        self.normalize = normalize or (lambda vector: vector)
        self._lock = asyncio.Lock()
        self._process = None
        self._closed = False
        self._pending = 0
        self._completed = 0

    @staticmethod
    async def _reap(process):
        if process is None:
            return
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()

    async def _stop(self):
        process, self._process = self._process, None
        self._completed = 0
        await self._reap(process)

    async def _start(self):
        if self._closed:
            raise unavailable()
        if self._process is not None and self._process.returncode is None:
            return
        await self._stop()
        process = await self.spawn()
        self._process = process
        if self._closed:
            await self._stop()
            raise unavailable()
        ready = json.loads(await process.stdout.readline())
        if ready != {"ready": True}:
            raise unavailable()

    async def start(self):
        owned = False
        try:
            async with asyncio.timeout(self.timeout):
                async with self._lock:
                    owned = True
                    await self._start()
        except BaseException:
            if owned:
                await self._stop()
            raise

    async def query(self, text):
        if self._closed:
            raise unavailable()
        if self._pending >= self.queue_size + 1:
            raise AppError(503, "AI_EMBEDDING_BUSY", "查询嵌入队列已满，请稍后重试")
        self._pending += 1
        owned = False
        try:
            async with asyncio.timeout(self.timeout):
                async with self._lock:
                    owned = True
                    await self._start()
                    request_id = uuid4().hex
                    process = self._process
                    process.stdin.write(json.dumps({"id": request_id, "text": text}).encode() + b"\n")
                    await process.stdin.drain()
                    result = json.loads(await process.stdout.readline())
                    if (
                        not isinstance(result, dict)
                        or result.get("id") != request_id
                        or "vector" not in result
                    ):
                        raise unavailable()
                    vector = self.normalize(result["vector"])
                    self._completed += 1
                    if self._completed >= 1000:
                        await self._stop()
                    return vector
        except asyncio.CancelledError:
            if owned:
                await self._stop()
            raise
        except TimeoutError:
            if owned:
                await self._stop()
            raise AppError(504, "AI_TIMEOUT", "查询嵌入处理超时，请稍后重试") from None
        except AppError:
            if owned:
                await self._stop()
            raise
        except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
            if owned:
                await self._stop()
            raise unavailable() from None
        finally:
            self._pending -= 1

    async def aclose(self):
        self._closed = True
        await self._stop()
