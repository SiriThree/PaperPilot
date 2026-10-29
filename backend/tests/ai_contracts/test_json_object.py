"""Explicit JSON object provider compatibility; all HTTP calls use a fixture."""

import asyncio
import json

import httpx
import pytest

from app.ai.models import Narrative
from app.ai.settings import AISettings
from app.errors import AppError
from app.schemas import QuestionRequest

from .real_provider import build_harness
from .support import configuration, model_client


def run(awaitable):
    return asyncio.run(awaitable)


def response(data):
    return httpx.Response(
        200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]}
    )


@pytest.mark.parametrize("mode", ["json_schema", "json_object"])
def test_response_format_and_strict_local_validation(mode):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return response({"insufficient_evidence": True, "statements": []})

    settings = configuration(response_format=mode)
    result = run(
        model_client(settings, handler).generate(Narrative, instruction="Follow instructions", payload={})
    )
    assert result.insufficient_evidence
    sent = requests[0]
    assert sent["response_format"]["type"] == mode
    if mode == "json_object":
        assert "JSON Schema" in sent["messages"][0]["content"]
        assert "insufficient_evidence" in sent["messages"][0]["content"]
        assert "json_schema" not in sent["response_format"]
    else:
        assert sent["response_format"]["json_schema"]["name"] == "Narrative"


@pytest.mark.parametrize("data", [{"statements": []}, {"insufficient_evidence": "yes", "statements": []}])
def test_json_object_rejects_invalid_shape(data):
    client = model_client(configuration(response_format="json_object"), lambda request: response(data))
    with pytest.raises(AppError) as exc:
        run(client.generate(Narrative, instruction="Return JSON", payload={}))
    assert exc.value.code == "AI_INVALID_RESPONSE"


def test_json_object_still_rejects_forged_citations(tmp_path):
    with build_harness(tmp_path=tmp_path) as harness:
        harness.ai.client = model_client(
            configuration(response_format="json_object"),
            lambda request: response(
                {
                    "insufficient_evidence": False,
                    "statements": [{"text": "Invented", "citation_ids": ["999"]}],
                }
            ),
        )
        with pytest.raises(AppError) as exc:
            run(harness.ai.answer(harness.ctx, QuestionRequest(question="Question"), [harness.source]))
        assert exc.value.code == "INVALID_CITATION"


def test_response_format_env_validation(monkeypatch):
    for key, value in {
        "PAPERPILOT_AI_BASE_URL": "https://model.invalid/v1",
        "PAPERPILOT_AI_API_KEY": "test-only",
        "PAPERPILOT_AI_CHAT_MODEL": "test-chat",
        "PAPERPILOT_AI_EMBEDDING_MODEL": "test-embedding",
        "PAPERPILOT_AI_EMBEDDING_DIMENSIONS": "3",
        "PAPERPILOT_AI_RESPONSE_FORMAT": "json_object",
    }.items():
        monkeypatch.setenv(key, value)
    assert AISettings.from_env().response_format == "json_object"
    monkeypatch.setenv("PAPERPILOT_AI_RESPONSE_FORMAT", "unsupported")
    with pytest.raises(RuntimeError, match="configuration"):
        AISettings.from_env()
