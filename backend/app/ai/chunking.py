"""Sentence/paragraph boundaries with an exact embedding-input budget.

Only original normalized-text slices become excerpts. No token decoding, LLM
rewriting, OCR, or joining of uncertain PDF hyphenation happens here.
"""

from bisect import bisect_left, bisect_right
import hashlib
import json
import re

import pysbd

from app.ai.token_budget import TokenBudget

MAX_EXCERPT = 4000
STRATEGY = "sentence-token-v2"


class ChunkingLimit(ValueError):
    pass


class ChunkCountLimit(ChunkingLimit):
    pass


def trimmed_span(text, start, end):
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


class SentenceTokenChunker:
    def __init__(self, directory, target_tokens=384, overlap_tokens=48):
        self.budget = TokenBudget(directory)
        if not 0 <= overlap_tokens < target_tokens <= self.budget.max_tokens - self.budget.count(""):
            raise ValueError("Invalid sentence chunk token budget")
        self.target, self.overlap = target_tokens, overlap_tokens
        self.segmenter = pysbd.Segmenter(language="en", clean=False, char_span=True)
        configuration = {
            "strategy": STRATEGY,
            "pysbd": pysbd.__version__,
            "tokenizer_sha256": self.budget.fingerprint,
            "target_tokens": target_tokens,
            "overlap_tokens": overlap_tokens,
            "max_input_tokens": self.budget.max_tokens,
            "max_excerpt_chars": MAX_EXCERPT,
        }
        self.configuration = configuration
        self.policy_id = (
            STRATEGY + ":" + hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
        )

    def _boundaries(self, text):
        paragraphs = {len(text)}
        sentences = {len(text)}
        starts = {0}
        previous = 0
        for match in [*re.finditer(r"\n[ \t]*\n+", text), None]:
            end = match.start() if match else len(text)
            paragraphs.add(end)
            paragraph = text[previous:end]
            # PDF line wrapping is not a sentence boundary. This same-length
            # view is only used for detection; excerpts keep their newlines.
            for span in self.segmenter.segment(paragraph.replace("\n", " ")):
                # Use boundary coordinates only; the original text is authoritative.
                if 0 <= span.start < span.end <= len(paragraph):
                    position = previous + span.end
                    if not (
                        position < len(text)
                        and text[position - 1].isascii()
                        and text[position - 1].isalpha()
                        and text[position].isascii()
                        and text[position].isalpha()
                    ):
                        sentences.add(position)
                        starts.add(previous + span.start)
            previous = match.end() if match else end
        for match in re.finditer(r"[。！？]+[”’」』\"')]*", text):
            sentences.add(match.end())
            starts.add(match.end())
        words = {match.start() for match in re.finditer(r"\s+", text)}
        word_starts = {match.end() for match in re.finditer(r"\s+", text)}
        clauses = {match.end() for match in re.finditer(r"[,;:，；：]", text)}
        return (
            [sorted(x) for x in [paragraphs, sentences, clauses, words]],
            sorted(starts),
            sorted(word_starts),
        )

    def _fits(self, text, start, end, prefix):
        excerpt = text[start:end]
        return (
            len(excerpt) <= MAX_EXCERPT
            and self.budget.count(excerpt, special=False) <= self.target
            and self.budget.count(prefix + excerpt) <= self.budget.max_tokens
        )

    def _fitting_end(self, text, start, prefix):
        cap = min(len(text), start + MAX_EXCERPT)
        if self._fits(text, start, cap, prefix):
            return cap
        offsets = self.budget.offsets(text[start:cap])
        candidates = sorted({start + end for _, end in offsets[: self.target] if end > 0})
        low, high, best = 0, len(candidates), None
        while low < high:
            mid = (low + high) // 2
            if self._fits(text, start, candidates[mid], prefix):
                best, low = candidates[mid], mid + 1
            else:
                high = mid
        if best is None:
            raise ChunkingLimit("No original-text character fits the embedding input budget")
        return best

    def _choose_end(self, text, start, maximum, boundaries, prefix):
        if maximum == len(text):
            return maximum, False
        # Prefer substantial complete semantic units; avoid very small blocks
        # when the first paragraph/heading happens to be short.
        floor = start + (maximum - start) // 2
        for group in boundaries:
            index = bisect_right(group, maximum) - 1
            while index >= 0 and group[index] > start:
                end = group[index]
                if end < floor:
                    break
                if self._fits(text, start, end, prefix):
                    return end, False
                index -= 1
        # A short block is preferable to mechanically cutting a word that can
        # fit intact at the beginning of the next block.
        words = boundaries[-1]
        index = bisect_right(words, maximum) - 1
        while index >= 0 and words[index] > start:
            if self._fits(text, start, words[index], prefix):
                return words[index], False
            index -= 1
        return maximum, True  # Explicit fallback for an indivisible URL/formula/word.

    def _next_start(self, text, start, end, sentence_starts, word_starts):
        if not self.overlap:
            return end
        offsets = self.budget.offsets(text[start:end])
        if len(offsets) <= self.overlap:
            return end
        desired = start + offsets[-self.overlap][0]
        for group in [sentence_starts, word_starts]:
            for candidate in group[bisect_left(group, desired) : bisect_left(group, end)]:
                if (
                    candidate > start
                    and self.budget.count(text[candidate:end], special=False) <= self.overlap
                ):
                    return candidate
        return end

    def split(self, text, title="", *, max_chunks=2500):
        prefix = title + "\n"
        if self.budget.count(prefix) >= self.budget.max_tokens:
            raise ChunkingLimit("Paper title exhausts the embedding input budget")
        boundaries, sentence_starts, word_starts = self._boundaries(text)
        chunks, start, previous_end = [], 0, 0
        while start < len(text):
            start, _ = trimmed_span(text, start, len(text))
            if start == len(text):
                break
            maximum = self._fitting_end(text, start, prefix)
            end, forced = self._choose_end(text, start, maximum, boundaries, prefix)
            actual_start, actual_end = trimmed_span(text, start, end)
            if actual_end <= previous_end:
                start = previous_end  # Drop overlap rather than emit an overlap-only block.
                continue
            excerpt = text[actual_start:actual_end]
            if not excerpt or not self._fits(text, actual_start, actual_end, prefix):
                raise ChunkingLimit("Cannot make a bounded original-text chunk")
            chunks.append(
                {
                    "offset": actual_start,
                    "end_offset": actual_end,
                    "excerpt": excerpt,
                    "forced_split": forced,
                    "policy_id": self.policy_id,
                }
            )
            if len(chunks) > max_chunks:
                raise ChunkCountLimit("PDF chunk count exceeded")
            previous_end = actual_end
            if end == len(text):
                break
            start = self._next_start(text, actual_start, end, sentence_starts, word_starts)
        return chunks
