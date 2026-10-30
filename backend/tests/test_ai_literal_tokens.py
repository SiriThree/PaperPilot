"""NLP technical tokens must not be mistaken for model-authored citations."""

import pytest

from app.ai.adapter import RealAI
from app.errors import AppError


def test_bert_token_literals_survive_text_validation():
    text = "BERT uses [MASK], [CLS], [SEP], [PAD] and [UNK] tokens."
    assert RealAI._text(text) == text


@pytest.mark.parametrize(
    "text", ["[MASK] predicts tokens [99]", "[CLS][1, 2]", "[MASK] [source]", "[[MASK]]"]
)
def test_literal_tokens_do_not_allow_model_authored_citations(text):
    with pytest.raises(AppError) as rejected:
        RealAI._text(text)
    assert rejected.value.code == "AI_INVALID_RESPONSE"
