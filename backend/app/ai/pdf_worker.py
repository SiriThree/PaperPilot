"""Private subprocess: parse bounded stdin, emit only chunks or a fixed error code."""

import io
import json
import re
import sys
from bisect import bisect_left, bisect_right

from pypdf import PdfReader

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 300
MAX_TEXT = 2_000_000
MAX_CHUNKS = 2500


def page_chunks(text, chunk_chars, overlap):
    """Yield exact page slices, preferring paragraphs, sentences, then whitespace.

    The size is a hard cap. Oversized sentences fall back to word boundaries;
    an unbroken token can only be split at the cap. Overlap is a target maximum,
    aligned forward to a boundary so the next excerpt does not start mid-word.
    """
    boundaries = [
        [m.end() for m in re.finditer(pattern, text)]
        for pattern in (
            r"\n[ \t]*\n+",
            r"""(?:[。！？；]+[”’」』）]*|[.!?;]+["'”’)]*(?=\s|$))\s*""",
            r"\s+",
        )
    ]
    start = 0
    while start < len(text):
        while start < len(text) and text[start].isspace():
            start += 1
        if start == len(text):
            break
        end = min(start + chunk_chars, len(text))
        if end < len(text):
            # Avoid tiny chunks from an early heading or short first sentence.
            lower = start + chunk_chars // 2
            for points in boundaries:
                index = bisect_right(points, end) - 1
                if index >= 0 and points[index] >= lower:
                    end = points[index]
                    break
        yield start, text[start:end].rstrip()
        if end == len(text):
            break
        next_start = end
        if overlap:
            target = max(start + 1, end - overlap)
            for points in boundaries:
                index = bisect_left(points, target)
                if index < len(points) and points[index] < end:
                    next_start = points[index]
                    break
        start = next_start


def parse(payload, chunk_chars, overlap):
    if not payload.startswith(b"%PDF-"):
        return {"error": "PDF_INVALID"}
    reader = PdfReader(io.BytesIO(payload), strict=False)
    if reader.is_encrypted:
        return {"error": "PDF_ENCRYPTED"}
    if len(reader.pages) > MAX_PAGES:
        return {"error": "PDF_LIMIT_EXCEEDED"}
    chunks, total = [], 0
    for number, page in enumerate(reader.pages, 1):
        # Preserve page boundaries and extracted text; no invented OCR or page-zero chunks.
        text = (page.extract_text() or "").replace("\x00", "").strip()
        total += len(text)
        if total > MAX_TEXT:
            return {"error": "PDF_LIMIT_EXCEEDED"}
        for offset, excerpt in page_chunks(text, chunk_chars, overlap):
            if excerpt:
                chunks.append({"page_number": number, "offset": offset, "excerpt": excerpt})
            if len(chunks) > MAX_CHUNKS:
                return {"error": "PDF_LIMIT_EXCEEDED"}
    return {"chunks": chunks} if chunks else {"error": "PDF_NO_TEXT"}


def main():
    try:
        # Linux/macOS: restrict decompression/parser memory and CPU in this child only.
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
        except (ImportError, OSError, ValueError):
            pass
        payload = sys.stdin.buffer.read(MAX_BYTES + 1)
        result = (
            {"error": "PDF_LIMIT_EXCEEDED"}
            if len(payload) > MAX_BYTES
            else parse(payload, int(sys.argv[1]), int(sys.argv[2]))
        )
    except Exception:
        result = {"error": "PDF_INVALID"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
