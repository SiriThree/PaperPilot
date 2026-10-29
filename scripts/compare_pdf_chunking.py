"""Compare both chunkers on pinned real PDFs; keep all full text in a local cache.

Each PDF runs in a bounded child process. The resulting v2 catalog can be
imported through the existing application API into a separate comparison DB.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
from importlib.metadata import version
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def word_cut(text, position):
    return bool(0 < position < len(text) and re.fullmatch("[A-Za-z]{2}", text[position - 1 : position + 1]))


def derive_one(args, paper, cache):
    from pypdf import PdfReader
    from app.ai.chunking import SentenceTokenChunker
    from app.ai.pdf_worker import (
        character_chunks,
        normalize_page_text,
        MAX_BYTES,
        MAX_PAGES,
        MAX_TEXT,
        MAX_CHUNKS,
    )

    payload = (args.pdf_dir / paper["filename"]).read_bytes()
    if len(payload) > MAX_BYTES or hashlib.sha256(payload).hexdigest() != paper["sha256"]:
        raise ValueError("Pinned PDF differs or exceeds input budget")
    chunker = SentenceTokenChunker(args.tokenizer_dir, args.target_tokens, args.overlap_tokens)
    reader = PdfReader(io.BytesIO(payload))
    if reader.is_encrypted or len(reader.pages) > MAX_PAGES:
        raise ValueError("Unsupported PDF")
    stats = {name: Counter() for name in ["character-v1", "sentence-token-v2"]}
    chunks = []
    total = 0
    input_tokens = {"character-v1": [], "sentence-token-v2": []}
    for number, page in enumerate(reader.pages, 1):
        text = normalize_page_text(page.extract_text() or "")
        total += len(text)
        if total > MAX_TEXT:
            raise ValueError("PDF text exceeds input budget")
        old = list(character_chunks(text, 1200, 120))
        new = chunker.split(text, paper["title"], max_chunks=MAX_CHUNKS - len(chunks))
        covered = bytearray(len(text))
        for mode, items in [("character-v1", old), ("sentence-token-v2", new)]:
            for c in items:
                if mode == "character-v1":
                    original = text[c["offset"] : c["offset"] + 1200]
                    start = c["offset"] + len(original) - len(original.lstrip())
                    end = start + len(c["excerpt"])
                else:
                    start, end = c["offset"], c["end_offset"]
                    assert text[start:end] == c["excerpt"]
                    covered[start:end] = b"\1" * (end - start)
                    chunks.append(dict(c, page_number=number))
                count = chunker.budget.count(paper["title"] + "\n" + c["excerpt"])
                input_tokens[mode].append(count)
                stats[mode].update(
                    chunks=1,
                    characters_including_overlap=len(c["excerpt"]),
                    over_limit=int(count > chunker.budget.max_tokens),
                    word_cut_starts=int(word_cut(text, start)),
                    word_cut_ends=int(word_cut(text, end)),
                    forced_splits=int(c.get("forced_split", False)),
                )
        missing = sum(not covered[i] and not char.isspace() for i, char in enumerate(text))
        if missing or stats["sentence-token-v2"]["over_limit"]:
            raise ValueError("New chunk coverage or token budget failed")
    if stats["character-v1"]["chunks"] != paper["chunk_count"]:
        raise ValueError("Legacy parser output differs from frozen baseline")
    result = {
        "arxiv_id": paper["arxiv_id"],
        "pdf_sha256": paper["sha256"],
        "policy": chunker.configuration,
        "policy_id": chunker.policy_id,
        "pages": len(reader.pages),
        "pages_with_text": len({c["page_number"] for c in chunks}),
        "statistics": {k: dict(v) for k, v in stats.items()},
        "input_token_counts": input_tokens,
        "chunks": chunks,
    }
    atomic_json(cache, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "demo/rag-300/catalog.json")
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--tokenizer-dir", type=Path, required=True)
    parser.add_argument("--target-tokens", type=int, default=384)
    parser.add_argument("--overlap-tokens", type=int, default=48)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".runtime/chunking-v2/parsed")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "demo/chunking-v2")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--derive-one", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--cache-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.derive_one:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
        resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
        derive_one(args, json.loads(args.derive_one.read_text()), args.cache_output)
        return
    if not 1 <= args.workers <= 8:
        parser.error("Use 1..8 bounded workers")
    baseline = json.loads(args.catalog.read_text())
    papers = baseline["papers"][: args.limit] if args.limit else baseline["papers"]
    code_hash = hashlib.sha256(
        b"".join(
            (ROOT / p).read_bytes()
            for p in [
                "backend/app/ai/chunking.py",
                "backend/app/ai/token_budget.py",
                "backend/app/ai/pdf_worker.py",
                "scripts/compare_pdf_chunking.py",
            ]
        )
    ).hexdigest()
    from app.ai.chunking import SentenceTokenChunker

    chunker = SentenceTokenChunker(args.tokenizer_dir, args.target_tokens, args.overlap_tokens)
    run_hash = hashlib.sha256((code_hash + chunker.policy_id).encode()).hexdigest()
    cache_root = args.cache_dir / run_hash[:16]
    cache_root.mkdir(parents=True, exist_ok=True)

    def collect(paper):
        if hashlib.sha256((args.pdf_dir / paper["filename"]).read_bytes()).hexdigest() != paper["sha256"]:
            raise ValueError("Pinned PDF changed since derivation")
        cache = cache_root / (paper["key"] + ".json")
        if not cache.exists():
            spec = cache_root / (paper["key"] + ".input.json")
            atomic_json(spec, paper)
            process = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--derive-one",
                    str(spec),
                    "--cache-output",
                    str(cache),
                    "--pdf-dir",
                    str(args.pdf_dir.resolve()),
                    "--tokenizer-dir",
                    str(args.tokenizer_dir.resolve()),
                    "--target-tokens",
                    str(args.target_tokens),
                    "--overlap-tokens",
                    str(args.overlap_tokens),
                ],
                capture_output=True,
                timeout=60,
                env={
                    **os.environ,
                    "PYTHONPATH": str(ROOT / "backend"),
                    "TOKENIZERS_PARALLELISM": "false",
                    "HF_HUB_OFFLINE": "1",
                },
            )
            if process.returncode != 0:
                raise RuntimeError(
                    f"Bounded real PDF comparison failed for {paper['arxiv_id']}; exit={process.returncode}; stderr={process.stderr.decode(errors='replace')[-600:]}"
                )
        result = json.loads(cache.read_text())
        if result["pdf_sha256"] != paper["sha256"] or result["policy_id"] != chunker.policy_id:
            raise ValueError("Cached derivation identity differs")
        return result

    totals = {name: Counter() for name in ["character-v1", "sentence-token-v2"]}
    tokens = {name: [] for name in totals}
    new_catalog = deepcopy(baseline)
    new_catalog.update(
        version="rag300-sentence-v2",
        target_count=len(papers),
        status="complete",
        chunking=chunker.configuration,
        based_on={
            "version": baseline["version"],
            "catalog_sha256": hashlib.sha256(args.catalog.read_bytes()).hexdigest(),
        },
        papers=[],
    )
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for paper, result in zip(papers, pool.map(collect, papers), strict=True):
            for mode in totals:
                totals[mode].update(result["statistics"][mode])
                tokens[mode].extend(result["input_token_counts"][mode])
            new = dict(
                paper,
                baseline_chunk_count=paper["chunk_count"],
                chunk_count=len(result["chunks"]),
                pages_with_text=result["pages_with_text"],
                chunk_characters_including_overlap=result["statistics"]["sentence-token-v2"][
                    "characters_including_overlap"
                ],
            )
            new_catalog["papers"].append(new)
            print(
                json.dumps(
                    {
                        "stage": "compared",
                        "count": len(new_catalog["papers"]),
                        "arxiv_id": paper["arxiv_id"],
                        "old_chunks": paper["chunk_count"],
                        "new_chunks": new["chunk_count"],
                    }
                ),
                flush=True,
            )
    new_catalog["projects"] = [
        dict(
            baseline["projects"][0],
            name=f"RAG 真实文献库（句段切块 {len(papers)} 篇）",
            description="句段边界、BGE token 预算与真实引用覆盖的独立对照语料。",
            paper_keys=[p["key"] for p in papers],
        )
    ]

    def percentiles(values):
        ordered = sorted(values)
        return {
            str(p): ordered[min(len(ordered) - 1, round((len(ordered) - 1) * p / 100))]
            for p in [0, 50, 95, 99, 100]
        }

    comparison = {
        "checked_at": datetime.now(UTC).isoformat(),
        "paper_count": len(papers),
        "code_sha256": code_hash,
        "baseline_catalog_sha256": hashlib.sha256(args.catalog.read_bytes()).hexdigest(),
        "policy": chunker.configuration,
        "policy_id": chunker.policy_id,
        "packages": {name: version(name) for name in ["pypdf", "pysbd", "tokenizers"]},
        "methods": {
            mode: dict(totals[mode], input_token_percentiles=percentiles(tokens[mode])) for mode in totals
        },
        "new_non_whitespace_characters_missing": 0,
        "exact_original_excerpt_spans_verified": True,
        "quality_metrics": "Not measured: no human relevance labels. This is boundary/coverage validation, not retrieval accuracy or search throughput.",
    }
    atomic_json(args.output_dir / "catalog.json", new_catalog)
    atomic_json(args.output_dir / "comparison.json", comparison)
    print(json.dumps(comparison, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
