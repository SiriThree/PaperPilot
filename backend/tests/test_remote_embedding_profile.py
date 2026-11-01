"""Provider migration checks use wire fixtures, never paid requests."""

import asyncio
import importlib.util
import json
from pathlib import Path
import runpy

import httpx
import pytest

from app.ai.client import ModelClient
from app.ai.adapter import RealAI
from app.errors import AppError
from ai_contracts.support import configuration


@pytest.mark.skipif(importlib.util.find_spec("tiktoken") is None, reason="Requires ai extra")
def test_openai_dimensions_and_all_input_validation_before_any_request(monkeypatch):
    import tiktoken

    calls = []

    def response(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(200, json={"data": [
            {"index": i, "embedding": [1.0, 0.0, 0.0]}
            for i in reversed(range(len(payload["input"])))
        ]})

    # Fixture tokenizer keeps unit tests offline; actual cl100k is checked in the real run.
    class Encoding:
        def encode_ordinary(self, text):
            return list(text)

    monkeypatch.setattr(tiktoken, "get_encoding", lambda name: Encoding())
    client = ModelClient(
        configuration(embedding_model="text-embedding-3-large"), transport=httpx.MockTransport(response)
    )
    assert len(asyncio.run(client.embed(["query"] * 33))) == 33
    assert [len(c["input"]) for c in calls] == [32, 1]
    assert all(c["dimensions"] == 3 and c["encoding_format"] == "float" for c in calls)
    calls.clear()
    for invalid in ["x" * 8193, "  "]:
        with pytest.raises(AppError) as exc:
            asyncio.run(client.embed(["query"] * 32 + [invalid]))
        assert exc.value.code == "AI_EMBEDDING_INPUT_LIMIT" and calls == []


def test_remote_launcher_uses_file_secret_without_reading_a_local_model(tmp_path):
    launcher = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/start_research_demo.py"))
    key = tmp_path / "provider.key"
    key.write_text("fixture-not-a-real-key")
    config = tmp_path / "embedding.json"
    config.write_text(json.dumps({
        "base_url": "https://4router.net/v1", "api_key_file": str(key),
        "model": "text-embedding-3-large", "dimensions": 3072,
        "revision": "operator-profile-v1", "chunk_tokenizer_dir": str(tmp_path),
    }))
    env = launcher["embedding_environment"](config, tmp_path / "missing-local-manifest.json")
    assert env["PAPERPILOT_AI_EMBEDDING_API_KEY"] == ""
    assert env["PAPERPILOT_AI_EMBEDDING_API_KEY_FILE"] == str(key)
    assert env["PAPERPILOT_AI_EMBEDDING_DIMENSIONS"] == "3072"
    assert env["PAPERPILOT_AI_LOCAL_MODEL_DIR"] == ""
    assert "fixture-not-a-real-key" not in repr(env)


def test_embedding_proxy_is_explicit_and_does_not_route_chat(tmp_path):
    settings = configuration(embedding_proxy_url="http://127.0.0.1:7890")
    ai = RealAI(object(), settings, reader=None)
    assert ai.embedder.proxy == "http://127.0.0.1:7890"
    assert ai.client.proxy is None
    assert ai.embedder.settings.base_url == settings.base_url


@pytest.mark.parametrize("proxy", [
    "http://external.invalid:7890", "http://user:secret@localhost:7890",
    "http://localhost:7890/path", "http://localhost", "http://localhost:99999",
])
def test_proxy_configuration_rejects_remote_hosts_and_url_credentials(proxy):
    with pytest.raises(ValueError):
        configuration(embedding_proxy_url=proxy)
