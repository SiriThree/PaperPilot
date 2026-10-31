"""Algorithm equivalence checks, not relevance judgments or corpus benchmarks."""

from uuid import UUID

import pytest

from app.ai.retrieval import bm25, bm25_topk, rank


@pytest.mark.parametrize("query", ["retrieval evidence", "检索增强生成", "ABC 123", "missing", "？！"])
@pytest.mark.parametrize("limit", [1, 10, 1500])
def test_streamed_bm25_preserves_full_corpus_scores_and_ties(query, limit):
    documents = [
        ["retrieval evidence " * (i % 4), "检索增强生成研究。", "ABC１２３", "unrelated"][i % 4]
        for i in range(1207)
    ]
    keys = [(UUID(int=1), 1, UUID(int=i + 1)) for i in range(len(documents))]
    calls = []

    def stream():
        calls.append(True)
        # Reverse order tests explicit stable ties rather than input/cursor order.
        yield from reversed(list(zip(keys, documents, strict=True)))

    expected_scores = bm25(query, documents)
    expected = [(keys[i], expected_scores[i]) for i in rank(expected_scores)[:limit]]
    actual = bm25_topk(query, stream, limit)
    assert actual == expected
    assert len(calls) in (0, 1, 2)


def test_streamed_bm25_empty_corpus():
    assert bm25_topk("evidence", lambda: iter(()), 10) == []
