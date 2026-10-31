"""Private model wire shapes; the model never supplies public Source objects."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CitedText(ModelOutput):
    text: str = Field(min_length=1, max_length=6000)
    citation_ids: list[str] = Field(min_length=1, max_length=50)


class Narrative(ModelOutput):
    insufficient_evidence: bool
    statements: list[CitedText] = Field(max_length=30)


class ExtractedField(ModelOutput):
    text: str | None = Field(
        max_length=6000,
        description="Concise supported claim for pending_verification; otherwise JSON null, without explanation.",
    )
    status: Literal["pending_verification", "not_mentioned", "insufficient_evidence"] = Field(
        description="pending_verification requires text and citations; the other states require null text and []."
    )
    citation_ids: list[str] = Field(
        max_length=50,
        description="Source ID strings, nonempty for pending_verification; otherwise an empty array.",
    )

    @model_validator(mode="after")
    def consistent_state(self):
        if self.status == "pending_verification":
            if not self.text or not self.text.strip() or not self.citation_ids:
                raise ValueError("Supported evidence requires nonempty text and citations")
        elif self.text is not None or self.citation_ids:
            raise ValueError("Absent evidence requires null text and no citations")
        return self


class Extraction(ModelOutput):
    research_question: ExtractedField
    method: ExtractedField
    results: ExtractedField
    conclusion: ExtractedField
    limitations: ExtractedField
