"""Protocol equivalence and invalidation; not a real-literature quality benchmark."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import chain
import math
import time
from uuid import UUID, uuid4

import pytest

from app.ai.lexical_index import LexicalCache, PaperLexicon, rank_lexicons
from app.ai.retrieval import bm25, rank


def rows(paper_id, texts):
    return [((paper_id, 1, UUID(int=i + 1)), text) for i, text in enumerate(texts)]


@pytest.mark.parametrize("query", ["retrieval evidence", "检索增强", "ABC 123", "missing", "？！"])
@pytest.mark.parametrize("selected", [(0, 1, 2), (0,), (1, 2), ()])
def test_inverted_matches_scoped_original_formula(query, selected):
    groups = [
        rows(
            UUID(int=i + 1),
            [
                ["retrieval evidence " * (j % 5 + 1), "检索增强研究。", "ABC１２３ unrelated"][j % 3]
                for j in range(403)
            ],
        )
        for i in range(3)
    ]
    blocks = [PaperLexicon.build(group, lambda: None, 10**8) for group in groups]
    chosen = list(chain.from_iterable(groups[i] for i in selected))
    expected_scores = bm25(query, [text for _, text in chosen])
    expected = sorted(
        [(chosen[i][0], score) for i, score in enumerate(expected_scores) if score > 0],
        key=lambda item: (-item[1], item[0]),
    )[:50]
    assert rank_lexicons(query, [blocks[i] for i in selected], 50, lambda: None) == expected


class Snapshot:
    def __init__(self):
        self.project = uuid4()
        self.paper_ids = [UUID(int=1), UUID(int=2)]
        self.version = [uuid4(), uuid4()]
        self.titles = ["Title one", "Title two"]
        self.selected = [0, 1]
        self.texts = [["rare evidence", "common filler"], ["common filler"]]
        self.loads = Counter()
        self.scans = 0
        self.closed = 0

    def check_deadline(self):
        return 10000

    def lexical_versions(self):
        return [(self.project, self.paper_ids[i], self.version[i], self.titles[i]) for i in self.selected]

    def lexical_documents(self, ids):
        try:
            for i in self.selected:
                if self.paper_ids[i] in ids:
                    self.loads[self.paper_ids[i]] += 1
                    yield from rows(self.paper_ids[i], [self.titles[i] + "\n" + t for t in self.texts[i]])
        finally:
            self.closed += 1

    def documents(self):
        self.scans += 1
        for i in self.selected:
            yield from rows(self.paper_ids[i], [self.titles[i] + "\n" + t for t in self.texts[i]])


def test_warm_queries_do_not_read_text_and_scope_stats_are_recomputed():
    snapshot, cache = Snapshot(), LexicalCache(10**7)
    first = cache.search(snapshot, "rare common", 50)
    assert first and cache.builds == 2
    snapshot.selected = [0]
    scoped = cache.search(snapshot, "rare common", 50)
    assert snapshot.loads == {p: 1 for p in snapshot.paper_ids}
    expected = bm25("rare common", [text for _, text in snapshot.documents()])
    assert [score for _, score in scoped] == [expected[i] for i in rank(expected)]
    assert not math.isclose(scoped[0][1], first[0][1])


def test_task_title_and_project_version_changes_invalidate_without_stale_hits():
    snapshot, cache = Snapshot(), LexicalCache(10**7)
    assert cache.search(snapshot, "rare", 10)
    snapshot.version[0] = uuid4()
    snapshot.texts[0] = ["replacement document"]
    assert not cache.search(snapshot, "rare", 10)
    assert snapshot.loads[snapshot.paper_ids[0]] == 2 and snapshot.loads[snapshot.paper_ids[1]] == 1
    snapshot.titles[0] = "rare title"
    assert cache.search(snapshot, "rare", 10)
    assert snapshot.loads[snapshot.paper_ids[0]] == 3
    snapshot.project = uuid4()
    cache.search(snapshot, "rare", 10)
    assert snapshot.loads[snapshot.paper_ids[0]] == 4


def test_budget_fallback_keeps_full_scope_and_closes_miss_reader():
    snapshot, cache = Snapshot(), LexicalCache(1)
    result = cache.search(snapshot, "rare", 10)
    assert result and snapshot.scans == 2 and snapshot.closed == 1
    assert cache.fallbacks == 1 and cache.size_bytes == 0


def test_eviction_bounds_accounted_bytes_and_can_rebuild():
    snapshot = Snapshot()
    sample = PaperLexicon.build(
        rows(snapshot.paper_ids[0], ["Title one\nrare evidence", "Title one\ncommon filler"]),
        lambda: None,
        100000,
    )
    cache = LexicalCache(sample.size_bytes + 5000)
    snapshot.selected = [0]
    assert cache.search(snapshot, "rare", 10)
    for _ in range(10):
        snapshot.version[0] = uuid4()
        assert cache.search(snapshot, "rare", 10)
        assert cache.size_bytes <= cache.max_bytes
    cache.clear()
    assert cache.size_bytes == 0


def test_concurrent_cold_requests_share_one_build():
    snapshot, cache = Snapshot(), LexicalCache(10**7)
    read = snapshot.lexical_documents

    def delayed(ids):
        time.sleep(0.02)
        yield from read(ids)

    snapshot.lexical_documents = delayed
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: cache.search(snapshot, "rare", 10), range(3)))
    assert results[0] == results[1] == results[2]
    assert snapshot.loads == {p: 1 for p in snapshot.paper_ids}
    assert cache.builds == 2
