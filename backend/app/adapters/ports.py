"""A/C/D replacement boundaries. Implement these ports; never import Mock from services."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, TypedDict
from uuid import UUID

from app.schemas import (
    AnswerResult,
    AuditEvent,
    AuditList,
    DraftResult,
    EvidenceResult,
    Member,
    Paper,
    ProcessResult,
    Project,
    ProjectCreate,
    QuestionRequest,
    ReportSave,
    ReportVersion,
    SearchRequest,
    SearchResult,
    SecureQueryResult,
    Source,
    Task,
    User,
)


@dataclass(frozen=True)
class Context:
    user_id: UUID
    project_id: UUID
    request_id: UUID


@dataclass(frozen=True)
class FileReference:
    storage_key: str
    paper_id: UUID
    project_id: UUID


@dataclass
class Job:
    task: Task
    context: Context
    payload: "TaskPayload"


TaskKind = Literal["process", "answer", "evidence", "draft"]
ProjectRole = Literal["owner", "member"]
StatisticsIntent = Literal["by_year", "by_topic", "missing_evidence"]
JobResult = ProcessResult | AnswerResult | EvidenceResult | DraftResult


class TaskPayload(TypedDict, total=False):
    """process: file + one paper_id; research: question + explicit paper_ids snapshot."""

    file: FileReference
    question: QuestionRequest
    paper_ids: list[UUID]


class DataPort(Protocol):
    """26-method A/B baseline; full semantics: docs/ab-data-contract.md.

    Contexts and worker Job snapshots are trusted server inputs, never HTTP payloads.
    Returned values are detached. Each mutation commits or rolls back independently.
    Worker completion/failure compares task_id + attempts + trusted context; stale writes fail.
    SQL execution, migrations, seed initialization and readiness are outside this port.
    """

    def user_by_name(self, username: str) -> User | None: ...
    def user_by_id(self, user_id: UUID) -> User | None: ...
    def role(self, user_id: UUID, project_id: UUID) -> ProjectRole | None: ...
    def projects(self, user_id: UUID) -> list[Project]: ...
    def create_project(self, user_id: UUID, data: ProjectCreate, request_id: UUID) -> Project: ...
    def members(self, ctx: Context) -> list[Member]: ...
    def add_member(self, ctx: Context, username: str) -> Member: ...
    def remove_member(self, ctx: Context, user_id: UUID) -> None: ...
    def papers(
        self, ctx: Context, year_from: int | None = None, year_to: int | None = None
    ) -> list[Paper]: ...
    def paper(self, ctx: Context, paper_id: UUID) -> Paper: ...
    def sources(self, ctx: Context, paper_id: UUID) -> list[Source]: ...
    def evidence(self, ctx: Context, paper_id: UUID) -> list[EvidenceResult]: ...
    def remove_paper(self, ctx: Context, paper_id: UUID) -> None: ...
    def import_paper(
        self, ctx: Context, title: str, year: int | None, digest: str, file: FileReference, key: str
    ) -> tuple[Paper, Task]: ...
    def create_task(self, ctx: Context, kind: TaskKind, payload: TaskPayload, key: str) -> Task: ...
    def task(self, ctx: Context, task_id: UUID) -> Task: ...
    def retry_task(self, ctx: Context, task_id: UUID) -> Task: ...
    def claim_job(self) -> Job | None: ...
    def complete_job(self, job: Job, result: JobResult) -> None: ...
    def fail_job(self, job: Job, code: str, message: str) -> None: ...
    def save_report(self, ctx: Context, data: ReportSave, key: str) -> ReportVersion: ...
    def versions(self, ctx: Context, report_id: UUID) -> list[ReportVersion]: ...
    def reports(self, ctx: Context) -> list[ReportVersion]: ...
    def controlled_statistics(
        self, ctx: Context, intent: StatisticsIntent
    ) -> tuple[list[str], list[dict]]: ...
    def append_audit(self, event: AuditEvent) -> None: ...
    def audit(
        self,
        ctx: Context,
        *,
        action: str | None,
        decision: str | None,
        actor_id: UUID | None,
        since: datetime | None,
        until: datetime | None,
        offset: int,
        limit: int,
    ) -> AuditList: ...


class AIPort(Protocol):
    """C returns structured results, never executes SQL or saves report versions."""

    async def process(self, ctx: Context, task_id: UUID, file: FileReference) -> ProcessResult: ...
    async def search(self, ctx: Context, query: SearchRequest) -> SearchResult: ...
    async def answer(
        self, ctx: Context, question: QuestionRequest, sources: list[Source]
    ) -> AnswerResult: ...
    async def extract(self, ctx: Context, paper_id: UUID, sources: list[Source]) -> EvidenceResult: ...
    async def draft(self, ctx: Context, question: QuestionRequest, sources: list[Source]) -> DraftResult: ...


class SecurityPort(Protocol):
    """D authorizes actions, owns all AI SQL validation/execution, and critical audit."""

    def authorize(self, ctx: Context, action: str, role: str) -> None: ...
    async def execute(self, ctx: Context, question: str) -> SecureQueryResult: ...


@dataclass(frozen=True)
class Adapters:
    data: DataPort
    ai: AIPort
    security: SecurityPort
