"""Original, generated PDFs and a wire-level model double; no paid APIs or user files."""

import io
import json
from dataclasses import replace

import httpx
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from app.ai.client import ModelClient
from app.ai.settings import AISettings


def pdf_bytes(pages):
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        if not text:
            continue
        font = writer._add_object(
            DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            }
        )
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 40 740 Td ({escaped}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def configuration(**changes):
    return replace(
        AISettings(
            base_url="https://model.invalid/v1",
            api_key="fixture-secret-not-a-key",
            chat_model="fixture-chat",
            embedding_model="fixture-embedding",
            embedding_dimensions=3,
        ),
        **changes,
    )


def model_response(request):
    body = json.loads(request.content)
    if request.url.path.endswith("/embeddings"):
        # A deterministic wire fixture, never used as a semantic quality evaluation.
        return httpx.Response(
            200,
            json={"data": [{"index": i, "embedding": [1.0, 0.1, 0.1]} for i, _ in enumerate(body["input"])]},
        )
    payload = json.loads(body["messages"][1]["content"])
    cid = payload["sources"][0]["citation_id"]
    if body["response_format"]["json_schema"]["name"] == "Extraction":
        result = {
            name: {
                "text": "A statement from the supplied excerpt, pending verification.",
                "status": "pending_verification",
                "citation_ids": [cid],
            }
            for name in ("research_question", "method", "results", "conclusion")
        }
        result["limitations"] = {"text": None, "status": "not_mentioned", "citation_ids": []}
    else:
        result = {
            "insufficient_evidence": False,
            "statements": [
                {
                    "text": "A statement supported by the supplied excerpt.",
                    "citation_ids": [cid],
                }
            ],
        }
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(result)},
                }
            ]
        },
    )


def model_client(settings=None, handler=model_response):
    return ModelClient(settings or configuration(), transport=httpx.MockTransport(handler))
