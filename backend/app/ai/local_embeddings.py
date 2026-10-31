import asyncio
import json
import math
import os
import sys
from pathlib import Path

from app.ai.client import invalid_response
from app.errors import AppError


class LocalEmbeddings:
    """Prepared ONNX model only. Inference runs in a cancellable CPU subprocess."""

    def __init__(self, settings):
        from app.ai.settings import TOKEN_CHUNK_MODEL

        if (
            settings.active_chunk_strategy == "sentence-token-v2"
            and settings.embedding_model != TOKEN_CHUNK_MODEL
        ):
            raise RuntimeError(
                "Sentence token policy is not validated for this model's input prefixes; startup refused"
            )
        try:
            from fastembed import TextEmbedding

            model = next(
                m for m in TextEmbedding.list_supported_models() if m["model"] == settings.embedding_model
            )
            root = settings.local_model_dir.resolve(strict=True)
            if model["dim"] != settings.embedding_dimensions or not all(
                (root / name).is_file() for name in [model["model_file"], "tokenizer.json", "config.json"]
            ):
                raise ValueError("Invalid local model")
        except Exception:
            raise RuntimeError("Local embedding model/dependencies not prepared; startup refused") from None
        self.settings, self.root = settings, root
        self.query_worker = None
        self.token_budget = None
        if settings.active_chunk_strategy == "sentence-token-v2":
            from app.ai.token_budget import TokenBudget

            self.token_budget = TokenBudget(root)

    def _guard(self, texts):
        if self.token_budget is not None:
            for text in texts:
                if self.token_budget.count(text) > self.token_budget.max_tokens:
                    raise AppError(
                        413, "AI_EMBEDDING_INPUT_LIMIT", "嵌入输入超过模型 token 上限，请缩小输入范围"
                    )

    def _normalize(self, vector):
        if (
            not isinstance(vector, list)
            or len(vector) != self.settings.embedding_dimensions
            or any(type(v) not in {int, float} or not math.isfinite(v) for v in vector)
        ):
            raise invalid_response()
        norm = math.hypot(*vector)
        if not norm or not math.isfinite(norm):
            raise invalid_response()
        return [v / norm for v in vector]

    async def _spawn_query_worker(self):
        return await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("embedding_worker.py")),
            self.settings.embedding_model,
            str(self.root),
            "--resident",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=2 * 1024**2,
            env={
                **os.environ,
                "HF_HUB_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "2",
                "TOKENIZERS_PARALLELISM": "false",
            },
        )

    def _query_worker(self):
        if self.query_worker is None:
            from app.ai.resident_embeddings import ResidentEmbeddingWorker

            self.query_worker = ResidentEmbeddingWorker(
                self._spawn_query_worker,
                self.settings.timeout_seconds,
                self.settings.embedding_queue_size,
                self._normalize,
            )
        return self.query_worker

    async def astart(self):
        if self.settings.query_embedding_worker == "resident":
            try:
                await self._query_worker().start()
            except Exception:
                raise RuntimeError("Local query embedding worker failed; startup refused") from None

    async def aclose(self):
        if self.query_worker is not None:
            await self.query_worker.aclose()

    async def embed_query(self, text):
        self._guard([text])
        if self.settings.query_embedding_worker == "oneshot":
            return (await self.embed([text]))[0]
        return await self._query_worker().query(text)

    async def embed(self, texts):
        self._guard(texts)
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(__file__).with_name("embedding_worker.py")),
                self.settings.embedding_model,
                str(self.root),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={
                    **os.environ,
                    "HF_HUB_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "OPENBLAS_NUM_THREADS": "1",
                    "OMP_NUM_THREADS": "2",
                    "TOKENIZERS_PARALLELISM": "false",
                },
            )
        except OSError:
            raise AppError(503, "AI_EMBEDDING_UNAVAILABLE", "本地嵌入服务暂不可用") from None
        try:
            output, _ = await process.communicate(json.dumps(texts).encode())
            if process.returncode != 0:
                raise AppError(503, "AI_EMBEDDING_UNAVAILABLE", "本地嵌入进程未完成，请稍后重试")
            try:
                vectors = json.loads(output)
                if len(vectors) != len(texts):
                    raise ValueError("invalid count")
                for vector in vectors:
                    if len(vector) != self.settings.embedding_dimensions or any(
                        type(v) not in {int, float} or not math.isfinite(v) for v in vector
                    ):
                        raise ValueError("invalid vector")
                    norm = math.hypot(*vector)
                    if not norm or not math.isfinite(norm):
                        raise ValueError("invalid norm")
                    vector[:] = [v / norm for v in vector]
                return vectors
            except (ValueError, TypeError, UnicodeError):
                raise invalid_response() from None
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
