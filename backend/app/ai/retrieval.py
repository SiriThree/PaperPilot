"""Deterministic BM25, cosine ranking and reciprocal-rank fusion; no database writes."""

import math
import heapq
import re
import unicodedata
from collections import Counter


def tokens(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    result = re.findall(r"[a-z0-9]+", text)
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        result.extend(run)
        result.extend(run[i : i + 2] for i in range(len(run) - 1))
    return result


def bm25(query, documents):
    terms = sorted(set(tokens(query)))
    counts = [Counter(tokens(text)) for text in documents]
    lengths = [sum(c.values()) for c in counts]
    average = sum(lengths) / max(len(counts), 1) or 1
    frequencies = Counter(term for c in counts for term in terms if term in c)
    return [
        sum(
            math.log(1 + (len(counts) - frequencies[t] + 0.5) / (frequencies[t] + 0.5))
            * c[t]
            * 2.5
            / (c[t] + 1.5 * (0.25 + 0.75 * length / average))
            for t in terms
            if c[t]
        )
        for c, length in zip(counts, lengths, strict=True)
    ]


def bm25_topk(query, documents, limit):
    """Same full-corpus BM25 as bm25(), with two snapshot scans and O(K+terms) state.

    documents() yields (stable_key, text) and must be repeatable within one database
    snapshot. Neither full texts, all term counters nor all scores are retained.
    """
    terms = sorted(set(tokens(query)))
    if not terms:
        return []
    frequencies = Counter()
    size = total_length = 0
    for _, text in documents():
        counts = Counter(tokens(text))
        size += 1
        total_length += sum(counts.values())
        frequencies.update(term for term in terms if counts[term])
    if not size or not frequencies:
        return []
    average = total_length / size or 1
    weights = {
        term: math.log(1 + (size - frequencies[term] + 0.5) / (frequencies[term] + 0.5)) for term in terms
    }

    def scores():
        for key, text in documents():
            counts = Counter(tokens(text))
            length = sum(counts.values())
            score = sum(
                weights[t] * counts[t] * 2.5 / (counts[t] + 1.5 * (0.25 + 0.75 * length / average))
                for t in terms
                if counts[t]
            )
            if score > 0:
                yield -score, key

    return [(key, -negative) for negative, key in heapq.nsmallest(limit, scores())]


def rank(scores, *, threshold=0):
    return sorted((i for i, score in enumerate(scores) if score > threshold), key=lambda i: (-scores[i], i))


def fuse(*rankings):
    scores = Counter()
    for ranking in rankings:
        for position, index in enumerate(ranking, 1):
            scores[index] += 1 / (60 + position)
    return scores
