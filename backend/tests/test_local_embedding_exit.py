import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai.local_embeddings import LocalEmbeddings
from app.errors import AppError


def test_killed_local_process_is_unavailable_instead_of_malformed_model_json(monkeypatch):
    process = SimpleNamespace(returncode=-9)

    async def communicate(payload):
        return b"", b""

    async def spawn(*args, **kwargs):
        return process

    process.communicate = communicate
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    embedder = LocalEmbeddings.__new__(LocalEmbeddings)
    embedder.root = Path("/isolated-fixture")
    embedder.settings = SimpleNamespace(embedding_model="fixture", embedding_dimensions=3)
    with pytest.raises(AppError) as exc:
        asyncio.run(embedder.embed(["fixture input"]))
    assert exc.value.status == 503 and exc.value.code == "AI_EMBEDDING_UNAVAILABLE"
