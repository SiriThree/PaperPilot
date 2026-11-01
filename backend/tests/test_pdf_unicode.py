import json
from types import SimpleNamespace

from app.ai import pdf_worker


def test_pdf_utf16_surrogates_become_valid_utf8_before_chunking(monkeypatch):
    # Real corpus paper 2402.02764 exposed this encoding path. This fixture
    # exercises both valid supplementary characters and malformed font maps.
    extracted = "Math \ud835\udc65, lone \ud835, ordinary 中文\x00."
    reader = SimpleNamespace(is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: extracted)])
    monkeypatch.setattr(pdf_worker, "PdfReader", lambda *args, **kwargs: reader)
    result = pdf_worker.parse(b"%PDF-fixture", 1200, 120)
    assert result["chunks"][0]["excerpt"] == "Math 𝑥, lone �, ordinary 中文."
    assert result["chunks"][0]["page_number"] == 1
    json.dumps(result, ensure_ascii=False).encode("utf-8")
