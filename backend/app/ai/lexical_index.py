"""Versioned, bounded derived TF indexes. PostgreSQL remains the source of truth.

Inspired by BM25S's inverted retrieval; no stemming, changed scoring variant or
precomputed global IDF. Each query recomputes N/df/average for its authorized scope.
"""

from array import array
from collections import Counter, OrderedDict, defaultdict
from contextlib import closing
from dataclasses import dataclass
import heapq
from itertools import groupby
import math
import sys
import threading

from app.ai.retrieval import bm25_topk, tokens
from app.errors import AppError

POLICY = "nfkc-ascii-cjk-unigram-bigram-v1"


class CapacityExceeded(Exception):
    pass


@dataclass(frozen=True)
class PaperLexicon:
    keys: tuple
    lengths: array
    document_ids: array
    frequencies: array
    ranges: dict
    total_length: int
    size_bytes: int

    @classmethod
    def build(cls, rows, check, max_bytes):
        keys, lengths, postings = [], array("I"), defaultdict(list)
        # This scratch budget is deliberately conservative and covers the temporary
        # Python postings before they are packed. Never truncate the documents.
        scratch = 0
        for key, text in rows:
            check()
            counts = Counter(tokens(text))
            scratch += 256 + len(counts) * 160 + sum(len(t.encode()) for t in counts)
            if scratch > max_bytes:
                raise CapacityExceeded
            doc_id = len(keys)
            keys.append(key)
            lengths.append(sum(counts.values()))
            for term, count in counts.items():
                postings[term].append((doc_id, count))
        ids, frequencies, ranges = array("I"), array("I"), {}
        for term, entries in postings.items():
            check()
            start = len(ids)
            for doc_id, count in entries:
                ids.append(doc_id)
                frequencies.append(count)
            ranges[term] = (start, len(ids))
        keys = tuple(keys)
        # Include owned arrays/containers, keys, UUIDs and lexical strings. This is
        # an accounting limit, not a whole-process RSS guarantee.
        size = sum(sys.getsizeof(v) for v in (keys, lengths, ids, frequencies, ranges))
        size += sum(sys.getsizeof(k) + sum(sys.getsizeof(v) for v in k) for k in keys)
        size += sum(
            sys.getsizeof(t) + sys.getsizeof(r) + sum(sys.getsizeof(v) for v in r) for t, r in ranges.items()
        )
        if size > max_bytes:
            raise CapacityExceeded
        return cls(keys, lengths, ids, frequencies, ranges, sum(lengths), size)


def rank_lexicons(query, blocks, limit, check):
    terms = sorted(set(tokens(query)))
    size = sum(len(b.keys) for b in blocks)
    if not terms or not size:
        return []
    average = sum(b.total_length for b in blocks) / size or 1
    # Preserve B0's sorted-term sum() order, including Python 3.12+ compensated
    # summation. Repeated += changes the last bits for multi-term CJK queries.
    contributions = defaultdict(lambda: array("d"))
    for term in terms:
        check()
        frequency = sum((r[1] - r[0]) for b in blocks if (r := b.ranges.get(term)))
        if not frequency:
            continue
        weight = math.log(1 + (size - frequency + 0.5) / (frequency + 0.5))
        for bi, block in enumerate(blocks):
            interval = block.ranges.get(term)
            if interval is None:
                continue
            start, end = interval
            for position in range(start, end):
                if position % 256 == 0:
                    check()
                doc_id, count = block.document_ids[position], block.frequencies[position]
                contributions[bi, doc_id].append(
                    weight * count * 2.5 / (count + 1.5 * (0.25 + 0.75 * block.lengths[doc_id] / average))
                )
    check()

    def scored_items():
        for (bi, di), values in contributions.items():
            score = sum(values)
            if score > 0:
                yield -score, blocks[bi].keys[di]

    return [(key, -score) for score, key in heapq.nsmallest(limit, scored_items())]


class LexicalCache:
    def __init__(self, max_bytes):
        self.max_bytes = max_bytes
        self._entries = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self._build_lock = threading.Lock()
        self.builds = 0
        self.fallbacks = 0

    def _read(self, identities):
        with self._lock:
            result = {}
            for identity in identities:
                if identity in self._entries:
                    self._entries.move_to_end(identity)
                    result[identity] = self._entries[identity]
            return result

    def _blocks(self, snapshot):
        identities = [(POLICY, *v) for v in snapshot.lexical_versions()]
        found = self._read(identities)
        if len(found) != len(identities):
            if not self._build_lock.acquire(timeout=snapshot.check_deadline() / 1000):
                raise AppError(504, "AI_TIMEOUT", "关键词索引准备超时，请稍后重试")
            try:
                # Another request may already have built the same versions.
                found = self._read(identities)
                missing = [identity for identity in identities if identity not in found]
                by_paper = {identity[2]: identity for identity in missing}
                total = sum(b.size_bytes for b in found.values())
                built = {}
                with closing(snapshot.lexical_documents(by_paper)) as documents:
                    for paper_id, rows in groupby(documents, key=lambda row: row[0][0]):
                        block = PaperLexicon.build(rows, snapshot.check_deadline, self.max_bytes - total)
                        total += block.size_bytes
                        built[by_paper[paper_id]] = block
                # Searchable versions with no root sources have an empty derived index.
                for identity in missing:
                    if identity not in built:
                        built[identity] = PaperLexicon.build(
                            (), snapshot.check_deadline, self.max_bytes - total
                        )
                        total += built[identity].size_bytes
                if total > self.max_bytes:
                    raise CapacityExceeded
                with self._lock:
                    for identity, block in built.items():
                        while self._entries and self._bytes + block.size_bytes > self.max_bytes:
                            _, evicted = self._entries.popitem(last=False)
                            self._bytes -= evicted.size_bytes
                        self._entries[identity] = block
                        self._bytes += block.size_bytes
                    self.builds += len(built)
                found.update(built)
            finally:
                self._build_lock.release()
        if sum(b.size_bytes for b in found.values()) > self.max_bytes:
            raise CapacityExceeded
        return [found[i] for i in identities]

    def search(self, snapshot, query, limit):
        try:
            return rank_lexicons(query, self._blocks(snapshot), limit, snapshot.check_deadline)
        except CapacityExceeded:
            # Full, correct streaming ranking is retained for a corpus exceeding
            # the cache budget; never rank only the cached prefix of the corpus.
            self.fallbacks += 1
            return bm25_topk(query, snapshot.documents, limit)

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    @property
    def size_bytes(self):
        with self._lock:
            return self._bytes
