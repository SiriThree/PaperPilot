"""Private subprocess: parse bounded stdin, emit only chunks or a fixed error code."""

import io
import json
import sys

from pypdf import PdfReader

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 300
MAX_TEXT = 2_000_000
MAX_CHUNKS = 2500


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
        # Some PDF ToUnicode maps yield UTF-16 surrogate code units. Combine
        # valid pairs and replace unpaired units before UTF-8 JSON/DB writes.
        text = (page.extract_text() or "").encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
        text = text.replace("\x00", "").strip()
        total += len(text)
        if total > MAX_TEXT:
            return {"error": "PDF_LIMIT_EXCEEDED"}
        for offset in range(0, len(text), chunk_chars - overlap):
            excerpt = text[offset : offset + chunk_chars].strip()
            if excerpt:
                chunks.append({"page_number": number, "offset": offset, "excerpt": excerpt})
            if len(chunks) > MAX_CHUNKS:
                return {"error": "PDF_LIMIT_EXCEEDED"}
            if offset + chunk_chars >= len(text):
                break
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
