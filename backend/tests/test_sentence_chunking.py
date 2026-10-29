"""Boundary, coverage and model-budget tests; fixtures are not research evidence."""

import asyncio
from dataclasses import replace
import json
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip("pysbd")
pytest.importorskip("tokenizers")

from tokenizers import Tokenizer, models, normalizers, pre_tokenizers, processors

from app.ai.chunking import ChunkCountLimit, ChunkingLimit, SentenceTokenChunker
from app.ai.local_embeddings import LocalEmbeddings
from app.ai.token_budget import TokenBudget
from app.ai.adapter import RealAI
from app.ai.files import LocalPDFReader
from app.errors import AppError
from ai_contracts.real_provider import build_harness
from ai_contracts.support import configuration


def make_tokenizer_directory(tmp_path):
    vocabulary = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", ".", ",", ";", ":", "/", "!", "?", "。", "！", "？"]
    vocabulary += list("abcdefghijklmnopqrstuvwxyz") + ["##" + c for c in "abcdefghijklmnopqrstuvwxyz"]
    vocabulary += "smith et al report points fig shows the result another sentence wraps over a line title this is complete evidence paragraph words preserve original text query context model retrieval generation".split()
    vocabulary = list(dict.fromkeys(vocabulary))
    tokenizer = Tokenizer(models.WordPiece(vocab={v: i for i, v in enumerate(vocabulary)}, unk_token="[UNK]"))
    tokenizer.normalizer = normalizers.BertNormalizer(lowercase=True)
    tokenizer.pre_tokenizer = pre_tokenizers.BertPreTokenizer()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 2), ("[SEP]", 3)]
    )
    tokenizer.save(str(tmp_path / "tokenizer.json"))
    (tmp_path / "tokenizer_config.json").write_text(json.dumps({"model_max_length": 64}))
    (tmp_path / "special_tokens_map.json").write_text(
        json.dumps({"cls_token": "[CLS]", "sep_token": "[SEP]", "pad_token": "[PAD]", "unk_token": "[UNK]"})
    )
    return tmp_path


@pytest.fixture
def tokenizer_directory(tmp_path):
    return make_tokenizer_directory(tmp_path)


def check_chunks(text, chunks, chunker, title=""):
    covered = set()
    previous_end = -1
    for chunk in chunks:
        start, end = chunk["offset"], chunk["end_offset"]
        assert chunk["excerpt"] == text[start:end] and end > previous_end
        assert 0 < len(chunk["excerpt"]) <= 4000
        assert chunker.budget.count(title + "\n" + chunk["excerpt"]) <= chunker.budget.max_tokens
        covered.update(range(start, end))
        previous_end = end
    assert all(index in covered for index, char in enumerate(text) if not char.isspace())


def test_original_slices_cover_text_with_bounded_overlap_and_no_ascii_word_cuts(tokenizer_directory):
    chunker = SentenceTokenChunker(tokenizer_directory, 32, 8)
    text = ("This is complete evidence. Another sentence preserves context.\n\n" * 15).strip()
    chunks = chunker.split(text, "A title")
    assert len(chunks) > 3
    check_chunks(text, chunks, chunker, "A title")
    overlaps = []
    for chunk in chunks:
        assert not chunk["forced_split"]
        for position in [chunk["offset"], chunk["end_offset"]]:
            assert not (
                0 < position < len(text) and re.fullmatch("[A-Za-z]{2}", text[position - 1 : position + 1])
            )
    for left, right in zip(chunks, chunks[1:]):
        overlap = text[right["offset"] : left["end_offset"]]
        overlaps.append(len(overlap))
        assert chunker.budget.count(overlap, special=False) <= 8
    assert any(overlaps)


def test_abbreviations_decimals_and_pdf_wrapped_lines_are_not_sentence_breaks(tokenizer_directory):
    chunker = SentenceTokenChunker(tokenizer_directory, 32, 4)
    text = "Smith et al. report 3.14 points. Fig. 2 shows the result. Another sentence wraps\nover a line."
    boundaries, _, _ = chunker._boundaries(text)
    endings = {text[:i].rstrip() for i in boundaries[1]}
    assert not any(ending.endswith(("et al.", "Fig.", "3.", "wraps")) for ending in endings)
    assert any(ending.endswith("points.") for ending in endings)
    check_chunks(text, chunker.split(text), chunker)


def test_title_is_included_in_hard_input_budget(tokenizer_directory):
    chunker = SentenceTokenChunker(tokenizer_directory, 32, 8)
    title = "title " * 40
    text = "This is complete evidence. " * 20
    chunks = chunker.split(text, title)
    check_chunks(text, chunks, chunker, title)
    assert any(chunker.budget.count(title + "\n" + c["excerpt"]) > 48 for c in chunks)
    with pytest.raises(ChunkingLimit, match="title"):
        chunker.split(text, "title " * 65)


def test_cjk_and_unbroken_strings_have_lossless_explicit_fallback(tokenizer_directory):
    chunker = SentenceTokenChunker(tokenizer_directory, 32, 8)
    for text in ["第一句话。第二句话！第三句话？" * 30, "https://host/" + "abcdefghij/" * 60, "a" * 9000]:
        chunks = chunker.split(text)
        check_chunks(text, chunks, chunker)
        if len(text) > 100:
            assert len(chunks) > 1
    assert any(c["forced_split"] for c in chunker.split("https://host/" + "abcdefghij/" * 60))
    with pytest.raises(ChunkCountLimit):
        chunker.split("This is complete evidence. " * 100, max_chunks=1)


def test_tokenizer_fingerprint_and_policy_change_with_budget(tokenizer_directory):
    a = SentenceTokenChunker(tokenizer_directory, 32, 8)
    b = SentenceTokenChunker(tokenizer_directory, 32, 4)
    assert a.budget.fingerprint == b.budget.fingerprint and a.policy_id != b.policy_id
    assert a.policy_id == SentenceTokenChunker(tokenizer_directory, 32, 8).policy_id


def test_model_input_guard_rejects_before_starting_inference(tokenizer_directory, monkeypatch):
    embedder = LocalEmbeddings.__new__(LocalEmbeddings)
    embedder.token_budget = TokenBudget(tokenizer_directory)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Oversized input must not reach inference")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(AppError) as exc:
        asyncio.run(embedder.embed(["title " * 80]))
    assert exc.value.code == "AI_EMBEDDING_INPUT_LIMIT"


def test_pdf_subprocess_preserves_pages_and_versions_chunk_ids(tokenizer_directory, tmp_path):
    with build_harness(tmp_path=tmp_path) as harness:
        old = asyncio.run(harness.ai.process(harness.ctx, harness.task_id, harness.file))
        settings = replace(
            configuration(),
            embedding_backend="fastembed",
            local_model_dir=tokenizer_directory,
            chunk_tokens=32,
            chunk_token_overlap=8,
        )
        ai = RealAI(harness.data, settings, reader=LocalPDFReader(tmp_path), embedder=SimpleNamespace())
        first = asyncio.run(ai.process(harness.ctx, harness.task_id, harness.file))
        again = asyncio.run(ai.process(harness.ctx, uuid4(), harness.file))
        assert first == again and {s.page_number for s in first.sources} == {1, 2}
        assert not {s.chunk_id for s in old.sources} & {s.chunk_id for s in first.sources}
        assert all(not s.synthetic for s in first.sources)
        budget = TokenBudget(tokenizer_directory)
        title = harness.data.paper(harness.ctx, harness.file.paper_id).title
        assert all(budget.count(title + "\n" + s.excerpt) <= 64 for s in first.sources)


def test_strategy_configuration_requires_matching_local_tokenizer(tokenizer_directory):
    assert configuration().active_chunk_strategy == "character-v1"
    assert (
        configuration(
            embedding_backend="fastembed", local_model_dir=tokenizer_directory
        ).active_chunk_strategy
        == "sentence-token-v2"
    )
    with pytest.raises(ValueError, match="local"):
        configuration(chunk_strategy="sentence-token-v2")
    with pytest.raises(ValueError, match="token budget"):
        configuration(chunk_tokens=32, chunk_token_overlap=32)
    with pytest.raises(ValueError, match="token budget"):
        SentenceTokenChunker(tokenizer_directory, 80, 8)
