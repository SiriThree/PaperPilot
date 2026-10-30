"""Extraction protocol and malformed-output regressions; no external model calls."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from app.errors import AppError
from app.schemas import QuestionRequest

from .real_provider import build_harness
from .support import configuration, model_client

FIELDS = ("research_question", "method", "results", "conclusion", "limitations")


def extraction(citation_id):
    result = {
        name: {
            "text": "A claim from the excerpt.",
            "status": "pending_verification",
            "citation_ids": [citation_id],
        }
        for name in FIELDS
    }
    result["limitations"] = {"text": None, "status": "not_mentioned", "citation_ids": []}
    return result


def response(data, *, finish_reason="stop"):
    return httpx.Response(
        200,
        json={"choices": [{"finish_reason": finish_reason, "message": {"content": json.dumps(data)}}]},
    )


@pytest.mark.parametrize("mode", ["json_schema", "json_object"])
def test_extraction_and_narrative_requests_use_separate_protocols(tmp_path, mode):
    requests = []
    with build_harness(tmp_path=tmp_path) as h:

        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            payload = json.loads(body["messages"][1]["content"])
            if "question" in payload:
                return response({"insufficient_evidence": True, "statements": []})
            return response(extraction(h.source.citation_id))

        h.ai.client = model_client(configuration(response_format=mode), handler)
        evidence = asyncio.run(h.ai.extract(h.ctx, h.source.paper_id, [h.source]))
        answer = asyncio.run(h.ai.answer(h.ctx, QuestionRequest(question="Question"), [h.source]))
        assert evidence.method.sources == [h.source]
        assert evidence.limitations.text is None and evidence.limitations.sources == []
        assert answer.insufficient_evidence
    extract_instruction = requests[0]["messages"][0]["content"]
    assert "insufficient_evidence=true" not in extract_instruction
    assert "statements=[]" not in extract_instruction
    assert '"text":null,"status":"not_mentioned","citation_ids":[]' in extract_instruction
    payload = json.loads(requests[0]["messages"][1]["content"])
    assert payload["output_language"] == "zh-CN"
    assert "Return the narrative object" in requests[1]["messages"][0]["content"]
    assert all(request["response_format"]["type"] == mode for request in requests)


@pytest.mark.parametrize("mode", ["json_schema", "json_object"])
@pytest.mark.parametrize(
    "field,code",
    [
        ({"text": "PRIVATE_RESPONSE", "status": "not_mentioned", "citation_ids": []}, "AI_INVALID_RESPONSE"),
        ({"text": None, "status": "not_mentioned", "citation_ids": ["1"]}, "AI_INVALID_RESPONSE"),
        ({"text": "", "status": "insufficient_evidence", "citation_ids": []}, "AI_INVALID_RESPONSE"),
        ({"text": None, "status": "pending_verification", "citation_ids": ["1"]}, "AI_INVALID_RESPONSE"),
        ({"text": " ", "status": "pending_verification", "citation_ids": ["1"]}, "AI_INVALID_RESPONSE"),
        ({"text": "Claim", "status": "pending_verification", "citation_ids": []}, "AI_INVALID_RESPONSE"),
        ({"text": "Claim", "status": "verified", "citation_ids": ["1"]}, "AI_INVALID_RESPONSE"),
        ({"text": "Claim", "status": "pending_verification", "citation_ids": [1]}, "AI_INVALID_RESPONSE"),
        ({"text": "Claim", "status": "pending_verification", "citation_ids": ["999"]}, "INVALID_CITATION"),
        ({"text": "Claim", "status": "pending_verification", "citation_ids": ["1", "1"]}, "INVALID_CITATION"),
        (
            {"text": "Claim [999]", "status": "pending_verification", "citation_ids": ["1"]},
            "AI_INVALID_RESPONSE",
        ),
    ],
)
def test_bad_field_is_rejected_without_retry_or_silent_repair(tmp_path, mode, field, code):
    calls = []
    with build_harness(tmp_path=tmp_path) as h:

        def handler(request):
            calls.append(1)
            data = extraction(h.source.citation_id)
            data["method"] = deepcopy(field)
            return response(data)

        h.ai.client = model_client(configuration(response_format=mode), handler)
        with pytest.raises(AppError) as rejected:
            asyncio.run(h.ai.extract(h.ctx, h.source.paper_id, [h.source]))
        assert rejected.value.code == code
        assert "PRIVATE_RESPONSE" not in rejected.value.message
        assert len(calls) == 1


@pytest.mark.parametrize("failure", ["truncated", "missing_field", "extra_field", "narrative"])
def test_partial_or_wrong_extraction_object_is_never_filled_in(tmp_path, failure):
    with build_harness(tmp_path=tmp_path) as h:

        def handler(request):
            data = extraction(h.source.citation_id)
            if failure == "missing_field":
                del data["limitations"]
            elif failure == "extra_field":
                data["sources"] = []
            elif failure == "narrative":
                data = {"insufficient_evidence": True, "statements": []}
            return response(data, finish_reason="length" if failure == "truncated" else "stop")

        h.ai.client = model_client(configuration(response_format="json_object"), handler)
        with pytest.raises(AppError) as rejected:
            asyncio.run(h.ai.extract(h.ctx, h.source.paper_id, [h.source]))
        assert rejected.value.code == "AI_INVALID_RESPONSE"
