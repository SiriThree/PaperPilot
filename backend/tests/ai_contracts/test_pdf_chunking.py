import pytest

from app.ai.pdf_worker import page_chunks, parse

from .support import pdf_bytes


@pytest.mark.parametrize("overlap", [0, 35, 120, 199])
@pytest.mark.parametrize(
    "text",
    [
        "Evidence retrieval preserves complete scientific terminology. " * 40,
        "First paragraph discusses provenance. " * 5 + "\n\n" + "Second paragraph has evidence. " * 25,
        "研究方法用于验证检索结果。结果需要人工核对！这是下一句的原文。" * 40,
        "An oversized sentence " + "terminology " * 100 + ". Final result.",
        "x" * 705,
        " \n\t ",
    ],
)
def test_chunks_preserve_text_offsets_coverage_and_bounds(text, overlap):
    chunks = list(page_chunks(text, 200, overlap))
    assert chunks == list(page_chunks(text, 200, overlap))
    covered = set()
    previous = -1
    for offset, excerpt in chunks:
        assert offset > previous
        assert 0 < len(excerpt) <= 200
        assert text[offset : offset + len(excerpt)] == excerpt
        covered.update(range(offset, offset + len(excerpt)))
        previous = offset
    assert all(i in covered for i, char in enumerate(text) if not char.isspace())


def test_sentence_endings_and_overlap_starts_are_complete():
    sentences = [f"Sentence {i} preserves scientific terminology and its evidence." for i in range(50)]
    text = " ".join(sentences)
    chunks = list(page_chunks(text, 200, 120))
    assert len(chunks) > 1
    for offset, excerpt in chunks:
        assert excerpt.startswith("Sentence ")
        assert excerpt.endswith("evidence.")
    assert chunks[1][0] < len(chunks[0][1])


def test_long_sentence_falls_back_to_whole_words():
    text = "terminology " * 100
    for offset, excerpt in page_chunks(text, 200, 35):
        assert excerpt.split() == ["terminology"] * len(excerpt.split())
        assert offset % len("terminology ") == 0


def test_paragraph_boundary_preferred_to_later_sentence():
    first = "Evidence stays in this paragraph. " * 4
    text = first + "\n\n" + "A different paragraph begins here. " * 20
    assert next(page_chunks(text, 200, 35))[1] == first.rstrip()


def test_pdf_parser_preserves_page_order_and_complete_words():
    result = parse(pdf_bytes(["terminology " * 100, "Last page."]), 200, 35)
    assert result["chunks"][-1] == {"page_number": 2, "offset": 0, "excerpt": "Last page."}
    assert all(
        c["excerpt"].split() == ["terminology"] * len(c["excerpt"].split()) for c in result["chunks"][:-1]
    )
