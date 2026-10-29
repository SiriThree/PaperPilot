"""Canonical public contracts. Export OpenAPI and generate TS; do not duplicate models."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorResponse(Schema):
    code: str
    message: str
    request_id: UUID
    details: dict[str, str] | None = None


class User(Schema):
    user_id: UUID
    username: str
    display_name: str


class LoginRequest(Schema):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr | None = Field(default=None, min_length=1, max_length=128)


class Identity(Schema):
    user: User
    mock: bool = True
    authentication: Literal["development_identity_only", "password"] = "development_identity_only"


class ProjectCreate(Schema):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)


class Project(Schema):
    project_id: UUID
    name: str
    description: str
    owner_id: UUID
    role: Literal["owner", "member"]
    mock: bool = True


class ProjectList(Schema):
    items: list[Project]
    mock: bool = True


class MemberInput(Schema):
    username: str = Field(min_length=1, max_length=64)


class Member(Schema):
    user: User
    role: Literal["owner", "member"]


class MemberList(Schema):
    items: list[Member]
    mock: bool = True


class Acknowledgement(Schema):
    message: str
    mock: bool = True


class Source(Schema):
    citation_id: str = Field(min_length=1, max_length=32)
    paper_id: UUID
    chunk_id: UUID
    page_number: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=4000)
    synthetic: bool = True


class Paper(Schema):
    paper_id: UUID
    project_id: UUID
    title: str
    year: int | None = None
    status: Literal["pending", "processing", "searchable", "failed"]
    mock: bool = True


class PaperList(Schema):
    items: list[Paper]
    mock: bool = True


class PaperDetail(Schema):
    paper: Paper
    sources: list[Source]
    mock: bool = True


class TaskStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    succeeded = "succeeded"
    failed = "failed"


class TaskError(Schema):
    code: str
    message: str


class SearchRequest(Schema):
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["keyword", "semantic", "hybrid"] = "hybrid"
    year_from: int | None = Field(default=None, ge=1000, le=3000)
    year_to: int | None = Field(default=None, ge=1000, le=3000)
    limit: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def year_order(self):
        if self.year_from is not None and self.year_to is not None and self.year_from > self.year_to:
            raise ValueError("invalid year range")
        return self


class SearchHit(Schema):
    paper: Paper
    source: Source
    score: float | None = None


class SearchResult(Schema):
    items: list[SearchHit]
    mode: Literal["keyword", "semantic", "hybrid"]
    mock: bool = True
    provider: str = "mock-c"


class QuestionRequest(Schema):
    question: str = Field(min_length=1, max_length=2000)
    paper_ids: list[UUID] = Field(default_factory=list, max_length=50)


class AnswerResult(Schema):
    kind: Literal["answer"] = "answer"
    answer: str
    citation_ids: list[str]
    sources: list[Source]
    insufficient_evidence: bool
    mock: bool = True


class EvidenceField(Schema):
    text: str | None
    status: Literal["not_mentioned", "insufficient_evidence", "pending_verification"]
    sources: list[Source]


class EvidenceResult(Schema):
    kind: Literal["evidence"] = "evidence"
    evidence_id: UUID
    paper_id: UUID
    research_question: EvidenceField
    method: EvidenceField
    results: EvidenceField
    conclusion: EvidenceField
    limitations: EvidenceField
    mock: bool = True


class EvidenceList(Schema):
    items: list[EvidenceResult]
    mock: bool = True


class DraftResult(Schema):
    kind: Literal["draft"] = "draft"
    body: str
    sources: list[Source]
    mock: bool = True


class ProcessResult(Schema):
    kind: Literal["process"] = "process"
    paper_id: UUID
    sources: list[Source]
    mock: bool = True


class ResultReference(Schema):
    resource_type: Literal["paper", "answer", "evidence", "draft"]
    resource_id: UUID


class Task(Schema):
    task_id: UUID
    project_id: UUID
    request_id: UUID
    kind: Literal["process", "answer", "evidence", "draft"]
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    attempts: int = Field(ge=0)
    error: TaskError | None = None
    result_ref: ResultReference | None = None
    result: (
        Annotated[ProcessResult | AnswerResult | EvidenceResult | DraftResult, Field(discriminator="kind")]
        | None
    ) = None
    mock: bool = True


class UploadResult(Schema):
    paper: Paper
    task: Task
    mock: bool = True


class ReportSave(Schema):
    title: str = Field(min_length=1, max_length=200)
    research_question: str = Field(min_length=1, max_length=2000)
    body: str = Field(min_length=1, max_length=100000)
    sources: list[Source] = Field(max_length=100)
    report_id: UUID | None = None
    base_version_id: UUID | None = None


class ReportVersion(Schema):
    report_id: UUID
    version_id: UUID
    project_id: UUID
    number: int
    title: str
    research_question: str
    body: str
    sources: list[Source]
    created_at: datetime
    mock: bool = True


class VersionList(Schema):
    items: list[ReportVersion]
    mock: bool = True


class SecureQueryRequest(Schema):
    question: str = Field(min_length=1, max_length=2000)


class QueryReason(Schema):
    code: str
    message: str


class SecureQueryResult(Schema):
    decision: Literal["allowed", "rejected", "undetermined"]
    execution_status: Literal["executed", "not_executed", "failed"]
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, str | int | float | bool | None]] = Field(default_factory=list)
    reason: QueryReason | None = None
    policy_id: str
    request_id: UUID
    mock: bool = True

    @model_validator(mode="after")
    def consistent(self):
        if self.execution_status == "executed" and self.decision != "allowed":
            raise ValueError("only allowed queries can be executed")
        if self.execution_status != "executed" and (self.rows or self.columns):
            raise ValueError("unexecuted queries cannot expose results")
        if self.decision == "rejected" and self.execution_status != "not_executed":
            raise ValueError("rejected query must not execute")
        if self.execution_status != "executed" and self.reason is None:
            raise ValueError("reason required")
        return self


class AuditEvent(Schema):
    event_id: UUID
    timestamp: datetime
    request_id: UUID
    actor_id: UUID | None
    project_id: UUID | None
    action: str
    resource_type: str
    decision: str
    policy_id: str | None = None
    execution_status: str
    duration_ms: float = Field(ge=0)
    mock: bool = True


class AuditList(Schema):
    items: list[AuditEvent]
    total: int
    offset: int
    limit: int
    mock: bool = True


class Health(Schema):
    status: Literal["ok"] = "ok"
    modes: dict[str, str]
    mock: bool
    limitations: list[str]
