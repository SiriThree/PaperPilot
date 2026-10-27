"""Private model wire shapes; the model never supplies public Source objects."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CitedText(ModelOutput):
    text: str = Field(min_length=1, max_length=6000)
    citation_ids: list[str] = Field(min_length=1, max_length=50)


class Narrative(ModelOutput):
    insufficient_evidence: bool
    statements: list[CitedText] = Field(max_length=30)


class ExtractedField(ModelOutput):
    text: str | None = Field(max_length=6000)
    status: Literal["pending_verification", "not_mentioned", "insufficient_evidence"]
    citation_ids: list[str] = Field(max_length=50)


class Extraction(ModelOutput):
    research_question: ExtractedField
    method: ExtractedField
    results: ExtractedField
    conclusion: ExtractedField
    limitations: ExtractedField
