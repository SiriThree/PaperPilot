"""Keep the published comparison tied to the same pinned PDFs and measured counts."""

import hashlib
import json
from pathlib import Path


def test_v2_comparison_uses_frozen_pdfs_and_keeps_coverage_claims_consistent():
    root = Path(__file__).resolve().parents[2]
    original = (root / "demo/rag-300/catalog.json").read_bytes()
    baseline = json.loads(original)
    catalog = json.loads((root / "demo/chunking-v2/catalog.json").read_text())
    comparison = json.loads((root / "demo/chunking-v2/comparison.json").read_text())
    previous = {p["key"]: p for p in baseline["papers"]}
    assert catalog["status"] == "complete" and len(catalog["papers"]) == comparison["paper_count"] == 300
    assert (
        catalog["based_on"]["catalog_sha256"]
        == comparison["baseline_catalog_sha256"]
        == hashlib.sha256(original).hexdigest()
    )
    assert {p["key"] for p in catalog["papers"]} == set(previous)
    for paper in catalog["papers"]:
        assert paper["sha256"] == previous[paper["key"]]["sha256"]
        assert paper["arxiv_id"] == previous[paper["key"]]["arxiv_id"]
        assert paper["version"] == previous[paper["key"]]["version"]
        assert paper["baseline_chunk_count"] == previous[paper["key"]]["chunk_count"]
    old = comparison["methods"]["character-v1"]
    new = comparison["methods"]["sentence-token-v2"]
    assert old["chunks"] == sum(p["chunk_count"] for p in baseline["papers"])
    assert new["chunks"] == sum(p["chunk_count"] for p in catalog["papers"])
    assert old["over_limit"] == 551 and new["over_limit"] == 0
    assert new["word_cut_starts"] == new["word_cut_ends"] == 0
    assert (
        comparison["exact_original_excerpt_spans_verified"]
        and comparison["new_non_whitespace_characters_missing"] == 0
    )
    assert new["input_token_percentiles"]["100"] <= catalog["chunking"]["max_input_tokens"] == 512


def test_persisted_v2_snapshot_matches_the_audited_corpus():
    root = Path(__file__).resolve().parents[2] / "demo/chunking-v2"
    catalog = (root / "catalog.json").read_bytes()
    summary = json.loads((root / "summary.json").read_text())
    check = json.loads((root / "ingestion-check.json").read_text())
    replay = json.loads((root / "idempotency-check.json").read_text())
    assert summary["catalog_sha256"] == check["catalog_sha256"] == hashlib.sha256(catalog).hexdigest()
    assert summary["paper_count"] == summary["process_indexes"] == check["paper_count"] == 300
    assert (
        summary["chunks"]
        == summary["embeddings"]
        == check["active_sources"]
        == check["unique_chunk_ids"]
        == 20986
    )
    assert summary["synthetic_chunks"] == check["synthetic_sources"] == check["over_limit"] == 0
    assert check["exact_matches_to_audited_page_excerpts"] and check["database_read_only"]
    assert check["max_actual_embedding_input_tokens"] <= check["model_input_limit"] == 512
    assert replay["unchanged"] and replay["before"] == replay["after"]
