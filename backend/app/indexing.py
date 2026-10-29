"""Private A/C integration values; public DataPort/AIPort and HTTP schemas stay stable."""

import math
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EmbeddingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    provider: str = Field(min_length=1, max_length=1000)
    model_name: str = Field(min_length=1, max_length=200)
    revision: str = Field(min_length=1, max_length=200)
    dimensions: int = Field(strict=True, ge=1, le=16384)
    distance_metric: str = "cosine"

    @model_validator(mode="after")
    def cosine_only(self):
        if self.distance_metric != "cosine":
            raise ValueError("only cosine embeddings supported")
        return self


class IndexedChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    chunk_id: UUID
    content: str = Field(min_length=1, max_length=4000)
    vector: list[float] = Field(min_length=1, max_length=16384)


class ProcessIndex(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: EmbeddingModel
    chunks: list[IndexedChunk] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def vectors_match_model(self):
        if len({c.chunk_id for c in self.chunks}) != len(self.chunks):
            raise ValueError("duplicate indexed chunk")
        for chunk in self.chunks:
            if (
                len(chunk.vector) != self.model.dimensions
                or any(not math.isfinite(v) for v in chunk.vector)
                or not math.isclose(math.hypot(*chunk.vector), 1, rel_tol=1e-5)
            ):
                raise ValueError("invalid normalized vector")
        return self
