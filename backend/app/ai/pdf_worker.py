"""Private subprocess: parse bounded stdin, emit only chunks or a fixed error code."""

import io
import json
import sys

from pypdf import PdfReader

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 300
MAX_TEXT = 2_000_000
MAX_CHUNKS = 2500


def normalize_page_text(text):
    return (
        text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace").replace("\x00", "").strip()
    )


def character_chunks(text, chunk_chars, overlap):
    for offset in range(0, len(text), chunk_chars - overlap):
        excerpt = text[offset : offset + chunk_chars].strip()
        if excerpt:
            yield {"offset": offset, "excerpt": excerpt}
        if offset + chunk_chars >= len(text):
            break


def parse(payload, chunk_chars, overlap, *, chunker=None, title=""):
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
        text = normalize_page_text(page.extract_text() or "")
        total += len(text)
        if total > MAX_TEXT:
            return {"error": "PDF_LIMIT_EXCEEDED"}
        if chunker is not None:
            from app.ai.chunking import ChunkCountLimit, ChunkingLimit

            try:
                page_chunks = chunker.split(text, title, max_chunks=MAX_CHUNKS - len(chunks))
            except ChunkCountLimit:
                return {"error": "PDF_LIMIT_EXCEEDED"}
            except ChunkingLimit:
                return {"error": "AI_CHUNK_INPUT_LIMIT"}
            chunks.extend(dict(chunk, page_number=number) for chunk in page_chunks)
            continue
        for chunk in character_chunks(text, chunk_chars, overlap):
            chunks.append(dict(chunk, page_number=number))
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
        chunker, options = None, {}
        if len(sys.argv) > 3:
            from pathlib import Path
            from app.ai.chunking import SentenceTokenChunker

            options = json.loads(sys.argv[3])
            chunker = SentenceTokenChunker(
                Path(options["tokenizer_dir"]), options["target_tokens"], options["overlap_tokens"]
            )
        payload = sys.stdin.buffer.read(MAX_BYTES + 1)
        result = (
            {"error": "PDF_LIMIT_EXCEEDED"}
            if len(payload) > MAX_BYTES
            else parse(
                payload, int(sys.argv[1]), int(sys.argv[2]), chunker=chunker, title=options.get("title", "")
            )
        )
    except Exception:
        result = {"error": "PDF_INVALID"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
