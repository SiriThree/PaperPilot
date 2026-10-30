"""AB-1 DataPort implemented on PostgreSQL 16 with psycopg3.

Behavior mirrors docs/ab-data-contract.md; layout follows docs/data-model/04-data-dictionary.md.
Each call owns one transaction; external AI calls never run inside it.
"""

import re
import json
import math
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from functools import wraps
from uuid import UUID, uuid4

import psycopg

from app.adapters.data_rules import (
    canonical,
    fingerprint,
    invalid,
    normalize_payload,
    normalize_result,
    validate_file,
    validate_key,
)
from app.adapters.ports import Context, Job
from app.data import mapping
from app.data.database import PostgresDatabase
from app.data.settings import DataSettings
from app.errors import AppError
from app.schemas import (
    AuditDetails,
    AuditEvent,
    AuditList,
    EvidenceField,
    EvidenceResult,
    Member,
    Paper,
    Project,
    ReportVersion,
    Task,
    TaskError,
)

EVIDENCE_FIELDS = ("research_question", "method", "results", "conclusion", "limitations")


def operation(write=False):
    def decorate(method):
        @wraps(method)
        def invoke(self, *args, **kwargs):
            with self.database.transaction(write=write) as conn:
                token = self._connection.set(conn)
                try:
                    result = method(self, *args, **kwargs)
                    if write and self._before_commit is not None:
                        self._before_commit(method.__name__, conn)
                    return result
                finally:
                    self._connection.reset(token)

        return invoke

    return decorate


def now():
    return datetime.now(UTC)


class PostgresData:
    def __init__(
        self, database: PostgresDatabase, settings: DataSettings, *, before_commit=None, claim_scope=None
    ):
        if not 1 <= settings.lease_seconds <= 3600:
            raise ValueError("Invalid lease duration")
        self.database = database
        self.settings = settings
        self._before_commit = before_commit
        if claim_scope is not None and (
            not isinstance(claim_scope, tuple)
            or len(claim_scope) != 2
            or any(not isinstance(value, UUID) for value in claim_scope)
        ):
            raise ValueError("Invalid trusted worker claim scope")
        self._claim_scope = claim_scope
        self._connection = ContextVar(f"pg_connection_{id(self)}")

    @property
    def _db(self):
        return self._connection.get()

    def _one(self, sql, values=()):
        return self._db.execute(sql, values).fetchone()

    def _rows(self, sql, values=()):
        return self._db.execute(sql, values).fetchall()

    def _executemany(self, sql, values):
        with self._db.cursor() as cursor:
            cursor.executemany(sql, values)

    # ---- shared scope/permission helpers ---------------------------------

    def _role(self, user_id, project_id):
        row = self._one(
            "SELECT role FROM project_members WHERE project_id=%s AND user_id=%s",
            (project_id, user_id),
        )
        return row["role"] if row else None

    def _scope(self, ctx, owner=False):
        role = self._role(ctx.user_id, ctx.project_id)
        if role is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        if owner and role != "owner":
            raise AppError(403, "OWNER_REQUIRED", "此操作需要项目负责人权限")

    def _paper(self, ctx, paper_id):
        row = self._one(
            "SELECT pp.paper_id, pp.project_id, p.title, p.year, pp.status"
            " FROM project_papers pp JOIN papers p ON p.metadata_id=pp.metadata_id"
            " WHERE pp.project_id=%s AND pp.paper_id=%s AND pp.removed_at IS NULL",
            (ctx.project_id, paper_id),
        )
        if row is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        return mapping.paper(row)

    # ---- idempotency and outbox ------------------------------------------

    def _replay(self, ctx, operation_name, key, digest):
        row = self._one(
            "SELECT request_fingerprint, response_snapshot FROM idempotency_records"
            " WHERE user_id=%s AND project_id=%s AND operation=%s AND key=%s",
            (ctx.user_id, ctx.project_id, operation_name, key),
        )
        if row and row["request_fingerprint"] != digest:
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同请求")
        return row["response_snapshot"] if row else None

    def _remember(self, ctx, operation_name, key, digest, response, **targets):
        self._db.execute(
            "INSERT INTO idempotency_records(user_id, project_id, operation, key,"
            " request_fingerprint, response_snapshot, paper_id, task_id, version_id)"
            " VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
            (
                ctx.user_id,
                ctx.project_id,
                operation_name,
                key,
                digest,
                canonical(response),
                targets.get("paper_id"),
                targets.get("task_id"),
                targets.get("version_id"),
            ),
        )

    def _outbox(self, action, *, ctx, resource_type, resource_id, execution_status="succeeded", details=None):
        self._db.execute(
            "INSERT INTO outbox_events(event_id, project_id, actor_id, request_id, payload)"
            " VALUES(%s,%s,%s,%s,%s::jsonb)",
            (
                uuid4(),
                ctx.project_id,
                ctx.user_id,
                ctx.request_id,
                canonical(
                    {
                        "action": action,
                        "resource_type": resource_type,
                        "resource_id": str(resource_id),
                        "decision": "allowed",
                        "execution_status": execution_status,
                        "duration_ms": 0,
                        "mock": False,
                        "details": (details or AuditDetails(resource_ids=[resource_id])).model_dump(
                            mode="json"
                        ),
                    }
                ),
            ),
        )

    # ---- task assembly ----------------------------------------------------

    @staticmethod
    def _result_view(row):
        """Normalize a joined task row into the result shape the mappers expect."""
        return {
            "task_id": row["task_id"],
            "project_id": row["project_id"],
            "kind": row["kind"],
            "payload": row["result_payload"],
            "paper_id": row["paper_id"],
            "evidence_id": row["evidence_id"],
        }

    def _task_view(self, row):
        if row["result_payload"] is None:
            return mapping.task(row)
        view = self._result_view(row)
        result = self._assemble_evidence(view) if row["kind"] == "evidence" else mapping.result_model(view)
        return mapping.task(row, result=result, result_ref_value=mapping.result_ref(row["kind"], view))

    def _assemble_evidence(self, result_row):
        fields = {
            row["field_name"]: row
            for row in self._rows(
                "SELECT field_name, text, status FROM evidence_fields WHERE project_id=%s AND task_id=%s",
                (result_row["project_id"], result_row["task_id"]),
            )
        }
        sources = {name: [] for name in EVIDENCE_FIELDS}
        for row in self._rows(
            "SELECT citation_id, paper_id, chunk_id, page_number, excerpt, synthetic, field_name"
            " FROM result_sources WHERE project_id=%s AND task_id=%s AND field_name IS NOT NULL"
            " ORDER BY ordinal",
            (result_row["project_id"], result_row["task_id"]),
        ):
            sources[row["field_name"]].append(mapping.source(row))
        return EvidenceResult(
            evidence_id=result_row["evidence_id"],
            paper_id=result_row["paper_id"],
            **{
                name: EvidenceField(
                    text=fields[name]["text"],
                    status=fields[name]["status"],
                    sources=sources[name],
                )
                for name in EVIDENCE_FIELDS
            },
            mock=result_row["payload"].get("mock", True),
        )

    def _task(self, ctx, task_id):
        row = self._one(
            "SELECT t.task_id, t.project_id, t.actor_id, t.request_id, t.kind, t.status, t.attempts,"
            " t.created_at, t.updated_at, t.error_code, t.error_message, t.payload,"
            " tr.payload AS result_payload, tr.paper_id, tr.evidence_id"
            " FROM tasks t LEFT JOIN task_results tr ON tr.task_id=t.task_id"
            " WHERE t.task_id=%s AND t.project_id=%s",
            (task_id, ctx.project_id),
        )
        if row is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        job = self._job_of(row)
        for paper_id in job.payload["paper_ids"]:
            self._paper(ctx, paper_id)
        return row, job

    def _job_of(self, row):
        return Job(
            self._task_view(row),
            Context(row["actor_id"], row["project_id"], row["request_id"]),
            mapping.task_payload(row["payload"]),
        )

    def _task_full_row(self, task_id):
        return self._one(
            "SELECT t.task_id, t.project_id, t.actor_id, t.request_id, t.kind, t.status, t.attempts,"
            " t.created_at, t.updated_at, t.error_code, t.error_message, t.payload,"
            " tr.payload AS result_payload, tr.paper_id, tr.evidence_id"
            " FROM tasks t LEFT JOIN task_results tr ON tr.task_id=t.task_id"
            " WHERE t.task_id=%s",
            (task_id,),
        )

    # ---- source validation ------------------------------------------------

    def _validate_sources(self, ctx, sources, allowed_ids=None):
        if len({s.citation_id for s in sources}) != len(sources):
            raise AppError(502, "INVALID_CITATION", "来源编号重复")
        for source in sources:
            self._paper(ctx, source.paper_id)
            if allowed_ids is not None and source.paper_id not in allowed_ids:
                raise AppError(502, "INVALID_CITATION", "来源超出任务范围")
            row = self._one(
                "SELECT page_number, excerpt, synthetic FROM chunks"
                " WHERE chunk_id=%s AND project_id=%s AND paper_id=%s",
                (source.chunk_id, ctx.project_id, source.paper_id),
            )
            if row is None or (row["page_number"], row["excerpt"], bool(row["synthetic"])) != (
                source.page_number,
                source.excerpt,
                source.synthetic,
            ):
                raise AppError(502, "INVALID_CITATION", "来源与授权片段不一致")

    def _job_record(self, job, *, allow_expired=False):
        # Serialize completion/failure with lease recovery on the same task row.
        self._one("SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE", (job.task.task_id,))
        row = self._task_full_row(job.task.task_id)
        if row is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        stored = self._job_of(row)
        if (
            stored.context.project_id != job.context.project_id
            or stored.context.user_id != job.context.user_id
        ):
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        if (
            stored.task.attempts != job.task.attempts
            or stored.context != job.context
            or stored.task.kind != job.task.kind
            or stored.task.project_id != job.task.project_id
            or fingerprint(stored.payload) != fingerprint(job.payload)
        ):
            self._state_conflict()
        if (
            stored.task.status == "processing"
            and not allow_expired
            and not self._one(
                "SELECT 1 FROM task_attempts WHERE task_id=%s AND attempt_no=%s"
                " AND status='processing' AND lease_expires_at > clock_timestamp()",
                (job.task.task_id, job.task.attempts),
            )
        ):
            self._state_conflict()
        return stored

    @staticmethod
    def _state_conflict():
        raise AppError(409, "TASK_STATE_CONFLICT", "任务执行次数或状态已变化")

    # ---- 26 DataPort methods ----------------------------------------------

    @operation()
    def user_by_name(self, username):
        return mapping.user(
            self._one("SELECT user_id, username, display_name FROM users WHERE username=%s", (username,))
        )

    @operation()
    def user_by_id(self, user_id):
        return mapping.user(
            self._one("SELECT user_id, username, display_name FROM users WHERE user_id=%s", (user_id,))
        )

    @operation()
    def role(self, user_id, project_id):
        return self._role(user_id, project_id)

    @operation()
    def projects(self, user_id):
        if self._one("SELECT 1 FROM users WHERE user_id=%s", (user_id,)) is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        return [
            Project(
                project_id=row["project_id"],
                name=row["name"],
                description=row["description"],
                owner_id=row["owner_id"],
                role=row["role"],
                mock=False,
            )
            for row in self._rows(
                "SELECT p.project_id, p.name, p.description, p.owner_id, m.role"
                " FROM projects p JOIN project_members m ON m.project_id=p.project_id"
                " WHERE m.user_id=%s ORDER BY p.created_at, p.project_id",
                (user_id,),
            )
        ]

    @operation(write=True)
    def create_project(self, user_id, data, request_id):
        if self._one("SELECT 1 FROM users WHERE user_id=%s", (user_id,)) is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        project_id = uuid4()
        timestamp = now()
        self._db.execute(
            "INSERT INTO projects(project_id, name, description, owner_id, created_at)"
            " VALUES(%s,%s,%s,%s,%s)",
            (project_id, data.name, data.description, user_id, timestamp),
        )
        self._db.execute(
            "INSERT INTO project_members(project_id, user_id, role, joined_at) VALUES(%s,%s,'owner',%s)",
            (project_id, user_id, timestamp),
        )
        ctx = Context(user_id, project_id, request_id)
        self._outbox("create_project", ctx=ctx, resource_type="project", resource_id=project_id)
        return Project(
            project_id=project_id,
            name=data.name,
            description=data.description,
            owner_id=user_id,
            role="owner",
            mock=False,
        )

    @operation()
    def members(self, ctx):
        self._scope(ctx, owner=True)
        return [
            Member(user=mapping.user(row), role=row["role"])
            for row in self._rows(
                "SELECT u.user_id, u.username, u.display_name, m.role"
                " FROM project_members m JOIN users u ON u.user_id=m.user_id"
                " WHERE m.project_id=%s ORDER BY u.username",
                (ctx.project_id,),
            )
        ]

    @operation(write=True)
    def add_member(self, ctx, username):
        self._scope(ctx, owner=True)
        user = mapping.user(
            self._one("SELECT user_id, username, display_name FROM users WHERE username=%s", (username,))
        )
        if user is None:
            raise AppError(404, "USER_NOT_FOUND", "账户不存在")
        # A repeated insert retains the existing owner/member role.
        if self._role(user.user_id, ctx.project_id) is None:
            self._db.execute(
                "INSERT INTO project_members(project_id, user_id, role, joined_at)"
                " VALUES(%s,%s,'member',now())",
                (ctx.project_id, user.user_id),
            )
            self._outbox("add_member", ctx=ctx, resource_type="project", resource_id=ctx.project_id)
        return Member(user=user, role=self._role(user.user_id, ctx.project_id))

    @operation(write=True)
    def remove_member(self, ctx, user_id):
        self._scope(ctx, owner=True)
        if self._role(user_id, ctx.project_id) == "owner":
            raise AppError(409, "OWNER_IMMUTABLE", "不能移除项目负责人")
        removed = self._db.execute(
            "DELETE FROM project_members WHERE project_id=%s AND user_id=%s",
            (ctx.project_id, user_id),
        ).rowcount
        if removed:
            self._outbox("remove_member", ctx=ctx, resource_type="project", resource_id=ctx.project_id)

    @operation()
    def papers(self, ctx, year_from=None, year_to=None):
        self._scope(ctx)
        if any(
            v is not None and (not isinstance(v, int) or not 1000 <= v <= 3000) for v in (year_from, year_to)
        ) or (year_from is not None and year_to is not None and year_from > year_to):
            raise invalid()
        return [
            mapping.paper(row)
            for row in self._rows(
                "SELECT pp.paper_id, pp.project_id, p.title, p.year, pp.status"
                " FROM project_papers pp JOIN papers p ON p.metadata_id=pp.metadata_id"
                " WHERE pp.project_id=%s AND pp.removed_at IS NULL"
                " AND (%s::int IS NULL OR p.year >= %s) AND (%s::int IS NULL OR p.year <= %s)"
                " ORDER BY pp.created_at, pp.paper_id",
                (ctx.project_id, year_from, year_from, year_to, year_to),
            )
        ]

    @operation()
    def paper(self, ctx, paper_id):
        self._scope(ctx)
        return self._paper(ctx, paper_id)

    @operation()
    def sources(self, ctx, paper_id):
        self._scope(ctx)
        self._paper(ctx, paper_id)
        row = self._one(
            "SELECT active_process_task_id FROM project_papers"
            " WHERE project_id=%s AND paper_id=%s AND removed_at IS NULL",
            (ctx.project_id, paper_id),
        )
        if row is None or row["active_process_task_id"] is None:
            return []
        return [
            mapping.source(s)
            for s in self._rows(
                "SELECT citation_id, paper_id, chunk_id, page_number, excerpt, synthetic"
                " FROM result_sources WHERE project_id=%s AND task_id=%s AND scope='root'"
                " ORDER BY ordinal",
                (ctx.project_id, row["active_process_task_id"]),
            )
        ]

    @operation()
    def evidence(self, ctx, paper_id):
        self._scope(ctx)
        self._paper(ctx, paper_id)
        return [
            self._assemble_evidence(row)
            for row in self._rows(
                "SELECT tr.task_id, tr.project_id, tr.kind, tr.payload, tr.paper_id, tr.evidence_id"
                " FROM task_results tr"
                " WHERE tr.project_id=%s AND tr.paper_id=%s AND tr.kind='evidence'"
                " ORDER BY tr.created_at, tr.task_id",
                (ctx.project_id, paper_id),
            )
        ]

    @operation(write=True)
    def remove_paper(self, ctx, paper_id):
        self._scope(ctx, owner=True)
        self._paper(ctx, paper_id)
        self._db.execute(
            "UPDATE project_papers SET removed_at=now(), updated_at=now()"
            " WHERE project_id=%s AND paper_id=%s AND removed_at IS NULL",
            (ctx.project_id, paper_id),
        )
        self._outbox("remove_paper", ctx=ctx, resource_type="paper", resource_id=paper_id)

    @operation(write=True)
    def import_paper(self, ctx, title, year, digest, file, key):
        self._scope(ctx)
        validate_file(ctx, file)
        validate_key(key)
        if (
            not isinstance(title, str)
            or not 1 <= len(title) <= 200
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or (year is not None and (not isinstance(year, int) or not 1000 <= year <= 3000))
        ):
            raise invalid()
        request_digest = fingerprint(f"{digest}:{title}:{year}")
        previous = self._replay(ctx, "upload", key, request_digest)
        if previous is not None:
            self._paper(ctx, UUID(previous["paper"]["paper_id"]))
            return Paper.model_validate(previous["paper"]), Task.model_validate(previous["task"])
        if self._one(
            "SELECT 1 FROM project_papers WHERE project_id=%s AND digest=%s",
            (ctx.project_id, digest),
        ):
            raise AppError(409, "DUPLICATE_PAPER", "当前项目已导入同一文件")
        if self._one("SELECT 1 FROM project_papers WHERE paper_id=%s", (file.paper_id,)):
            raise AppError(409, "IDENTIFIER_CONFLICT", "资源编号已使用")
        timestamp = now()
        metadata_id = uuid4()
        try:
            self._db.execute(
                "INSERT INTO papers(metadata_id, project_id, title, year, created_at) VALUES(%s,%s,%s,%s,%s)",
                (metadata_id, ctx.project_id, title, year, timestamp),
            )
            self._db.execute(
                "INSERT INTO project_papers(paper_id, project_id, metadata_id, imported_by, digest,"
                " storage_key, status, index_state, created_at, updated_at)"
                " VALUES(%s,%s,%s,%s,%s,%s,'pending','pending',%s,%s)",
                (
                    file.paper_id,
                    ctx.project_id,
                    metadata_id,
                    ctx.user_id,
                    digest,
                    file.storage_key,
                    timestamp,
                    timestamp,
                ),
            )
        except psycopg.errors.IntegrityError as error:
            # A concurrent winner can commit between the pre-checks and this INSERT; the immediate
            # unique key decides the public code (digest -> DUPLICATE_PAPER, otherwise the id).
            name = error.diag.constraint_name if error.diag is not None else None
            if name == "project_papers_digest_unique":
                raise AppError(409, "DUPLICATE_PAPER", "当前项目已导入同一文件") from None
            raise AppError(409, "IDENTIFIER_CONFLICT", "资源编号已使用") from None
        task = self._create_task(ctx, "process", {"file": file, "paper_ids": [file.paper_id]}, key)
        paper = self._paper(ctx, file.paper_id)
        result = paper, task
        self._remember(
            ctx,
            "upload",
            key,
            request_digest,
            {"paper": paper.model_dump(mode="json"), "task": task.model_dump(mode="json")},
            paper_id=paper.paper_id,
            task_id=task.task_id,
        )
        self._outbox("import_paper", ctx=ctx, resource_type="paper", resource_id=paper.paper_id)
        return result

    @operation(write=True)
    def create_task(self, ctx, kind, payload, key):
        self._scope(ctx)
        return self._create_task(ctx, kind, payload, key)

    @operation(write=True)
    def reindex_paper(self, ctx, paper_id, key):
        """Explicit owner-only operational entry; retain historical chunks and model versions."""
        self._scope(ctx, owner=True)
        self._paper(ctx, paper_id)
        validate_key(key)
        row = self._one(
            "SELECT storage_key FROM project_papers WHERE project_id=%s AND paper_id=%s FOR UPDATE",
            (ctx.project_id, paper_id),
        )
        from app.adapters.ports import FileReference

        payload = {
            "file": FileReference(row["storage_key"], paper_id, ctx.project_id),
            "paper_ids": [paper_id],
        }
        previous = self._replay(ctx, "process", key, fingerprint(payload))
        if previous is not None:
            return Task.model_validate(previous)
        if self._one(
            "SELECT 1 FROM tasks t JOIN task_papers tp USING(project_id,task_id)"
            " WHERE tp.project_id=%s AND tp.paper_id=%s AND t.kind='process'"
            " AND t.status IN ('pending','processing')",
            (ctx.project_id, paper_id),
        ):
            raise AppError(409, "PROCESS_IN_PROGRESS", "论文已有待处理或正在执行的任务，请先核对原任务")
        return self._create_task(ctx, "process", payload, key)

    def _create_task(self, ctx, kind, payload, key):
        validate_key(key)
        payload = normalize_payload(ctx, kind, payload)
        for paper_id in payload["paper_ids"]:
            self._paper(ctx, paper_id)
        digest = fingerprint(payload)
        previous = self._replay(ctx, kind, key, digest)
        if previous is not None:
            return Task.model_validate(previous)
        task_id, timestamp = uuid4(), now()
        self._db.execute(
            "INSERT INTO tasks(task_id, project_id, actor_id, request_id, kind, status, attempts,"
            " payload, payload_fingerprint, created_at, updated_at)"
            " VALUES(%s,%s,%s,%s,%s,'pending',0,%s::jsonb,%s,%s,%s)",
            (
                task_id,
                ctx.project_id,
                ctx.user_id,
                ctx.request_id,
                kind,
                canonical(payload),
                digest,
                timestamp,
                timestamp,
            ),
        )
        for paper_id in payload["paper_ids"]:
            self._db.execute(
                "INSERT INTO task_papers(task_id, paper_id, project_id) VALUES(%s,%s,%s)",
                (task_id, paper_id, ctx.project_id),
            )
        task = self._task_view(self._task_full_row(task_id))
        self._remember(ctx, kind, key, digest, task.model_dump(mode="json"), task_id=task_id)
        self._outbox("create_task", ctx=ctx, resource_type="task", resource_id=task_id)
        return task

    @operation()
    def task(self, ctx, task_id):
        self._scope(ctx)
        row, _ = self._task(ctx, task_id)
        return self._task_view(row)

    @operation(write=True)
    def retry_task(self, ctx, task_id):
        self._scope(ctx)
        _, job = self._task(ctx, task_id)
        if job.task.status != "failed":
            raise AppError(409, "TASK_NOT_RETRYABLE", "仅失败任务可以重试")
        self._db.execute(
            "UPDATE tasks SET status='pending', actor_id=%s, request_id=%s, updated_at=%s,"
            " error_code=NULL, error_message=NULL, recovery_failures=0"
            " WHERE task_id=%s AND project_id=%s AND status='failed'",
            (ctx.user_id, ctx.request_id, now(), task_id, ctx.project_id),
        )
        if job.task.kind == "process":
            self._db.execute(
                "UPDATE project_papers SET status='pending', updated_at=now()"
                " WHERE project_id=%s AND paper_id=%s",
                (ctx.project_id, job.payload["file"].paper_id),
            )
        self._outbox("retry_task", ctx=ctx, resource_type="task", resource_id=task_id)
        return self._task_view(self._task_full_row(task_id))

    @operation(write=True)
    def claim_job(self):
        scope_filter = " AND t.project_id=%s AND t.actor_id=%s" if self._claim_scope else ""
        row = self._one(
            "SELECT t.task_id, t.status, t.recovery_failures FROM tasks t WHERE (t.status='pending'"
            " OR (t.status='processing' AND EXISTS (SELECT 1 FROM task_attempts a"
            "     WHERE a.task_id=t.task_id AND a.status='processing'"
            "     AND a.lease_expires_at <= now())))"
            + scope_filter
            + " ORDER BY t.created_at, t.task_id LIMIT 1 FOR UPDATE SKIP LOCKED",
            self._claim_scope or (),
        )
        if row is None:
            return None
        if (
            row["status"] == "processing"
            and row["recovery_failures"] >= self.settings.max_automatic_attempts - 1
        ):
            job = self._job_of(self._task_full_row(row["task_id"]))
            self._fail_job(job, "TASK_RETRY_LIMIT", "自动恢复次数已达上限，请核对后明确重试", expired=True)
            return None
        # Expire the stale attempt first: only one processing attempt per task may exist.
        self._db.execute(
            "UPDATE task_attempts SET status='expired', finished_at=now()"
            " WHERE task_id=%s AND status='processing'",
            (row["task_id"],),
        )
        self._db.execute(
            "UPDATE tasks SET status='processing', attempts=attempts+1, recovery_failures=recovery_failures+%s, updated_at=now() WHERE task_id=%s",
            (int(row["status"] == "processing"), row["task_id"]),
        )
        task_row = self._one("SELECT * FROM tasks WHERE task_id=%s", (row["task_id"],))
        lease_until = now() + timedelta(seconds=self.settings.lease_seconds)
        self._db.execute(
            "INSERT INTO task_attempts(task_id, attempt_no, project_id, actor_id, request_id,"
            " status, claimed_at, lease_expires_at)"
            " VALUES(%s,%s,%s,%s,%s,'processing',now(),%s)",
            (
                task_row["task_id"],
                task_row["attempts"],
                task_row["project_id"],
                task_row["actor_id"],
                task_row["request_id"],
                lease_until,
            ),
        )
        job = self._job_of(self._task_full_row(task_row["task_id"]))
        if job.task.kind == "process":
            self._db.execute(
                "UPDATE project_papers SET status='processing', updated_at=now()"
                " WHERE project_id=%s AND paper_id=%s",
                (job.context.project_id, job.payload["file"].paper_id),
            )
        return job

    def _require_live_lease(self, job):
        row = self._one(
            "SELECT 1 FROM task_attempts WHERE task_id=%s AND attempt_no=%s"
            " AND status='processing' AND lease_expires_at>now()",
            (job.task.task_id, job.task.attempts),
        )
        if row is None:
            self._state_conflict()

    @operation(write=True)
    def renew_job(self, job):
        stored = self._job_record(job)
        self._scope(job.context)
        if stored.task.status != "processing":
            self._state_conflict()
        self._require_live_lease(job)
        self._db.execute(
            "UPDATE task_attempts SET lease_expires_at=%s WHERE task_id=%s AND attempt_no=%s",
            (now() + timedelta(seconds=self.settings.lease_seconds), job.task.task_id, job.task.attempts),
        )

    @operation(write=True)
    def complete_job(self, job, result):
        self._complete_job(job, result)

    @operation(write=True)
    def complete_indexed_job(self, job, result, index):
        from app.indexing import ProcessIndex
        from pydantic import ValidationError

        try:
            index = ProcessIndex.model_validate(
                index.model_dump(warnings=False) if isinstance(index, ProcessIndex) else index
            )
        except ValidationError:
            raise AppError(502, "ADAPTER_CONTRACT_ERROR", "索引维度或向量不符合契约") from None
        if (
            job.task.kind != "process"
            or {c.chunk_id: c.content for c in index.chunks}
            != {s.chunk_id: s.excerpt for s in result.sources}
            or result.mock
            or any(s.synthetic for s in result.sources)
        ):
            raise AppError(502, "ADAPTER_CONTRACT_ERROR", "索引与处理来源不一致")
        self._complete_job(job, result, index)

    def _complete_job(self, job, result, index=None):
        stored = self._job_record(job)
        self._scope(job.context)
        for pid in job.payload["paper_ids"]:
            self._paper(job.context, pid)
        result = normalize_result(job.task.kind, result)
        if stored.task.status == "succeeded" and fingerprint(stored.task.result) == fingerprint(result):
            # Mock results have no vector index; restricted Mock workers need no index-table grant.
            saved = (
                self._one("SELECT fingerprint FROM process_indexes WHERE task_id=%s", (job.task.task_id,))
                if job.task.kind == "process" and not result.mock
                else None
            )
            if (saved is not None) != (index is not None) or (
                saved and saved["fingerprint"] != fingerprint(index)
            ):
                self._state_conflict()
            return
        if stored.task.status != "processing":
            self._state_conflict()
        self._require_live_lease(job)
        if job.task.kind == "process":
            if (
                result.paper_id != job.payload["file"].paper_id
                or not result.sources
                or any(s.paper_id != result.paper_id for s in result.sources)
                or len({s.chunk_id for s in result.sources}) != len(result.sources)
                or len({s.citation_id for s in result.sources}) != len(result.sources)
            ):
                raise AppError(502, "ADAPTER_CONTRACT_ERROR", "论文处理结果不符合契约")
            for source in result.sources:
                self._db.execute(
                    "INSERT INTO chunks(chunk_id, project_id, paper_id, page_number, excerpt,"
                    " content, synthetic, created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,now())"
                    " ON CONFLICT (chunk_id) DO NOTHING",
                    (
                        source.chunk_id,
                        job.context.project_id,
                        result.paper_id,
                        source.page_number,
                        source.excerpt,
                        source.excerpt if index is not None else None,
                        source.synthetic,
                    ),
                )
                saved = self._one("SELECT * FROM chunks WHERE chunk_id=%s", (source.chunk_id,))
                if any(
                    saved[key] != value
                    for key, value in {
                        "project_id": job.context.project_id,
                        "paper_id": result.paper_id,
                        "page_number": source.page_number,
                        "excerpt": source.excerpt,
                        "content": source.excerpt if index is not None else None,
                        "synthetic": source.synthetic,
                    }.items()
                ):
                    raise AppError(502, "INVALID_CITATION", "片段标识已用于不同来源")
        elif job.task.kind == "evidence":
            if result.paper_id != job.payload["paper_ids"][0]:
                raise AppError(502, "ADAPTER_CONTRACT_ERROR", "证据论文与任务不一致")
            for name in EVIDENCE_FIELDS:
                field = getattr(result, name)
                self._validate_sources(job.context, field.sources, [result.paper_id])
                if field.status == "pending_verification" and (not field.text or not field.sources):
                    raise AppError(502, "INVALID_CITATION", "证据缺少来源")
        else:
            self._validate_sources(job.context, result.sources, job.payload["paper_ids"])
            if job.task.kind == "answer" and (
                len(set(result.citation_ids)) != len(result.citation_ids)
                or set(result.citation_ids) != {s.citation_id for s in result.sources}
                or (not result.insufficient_evidence and not result.sources)
            ):
                raise AppError(502, "INVALID_CITATION", "回答缺少有效引用")
        self._commit_result(job, result)
        if index is not None:
            self._commit_index(job, index)
        self._outbox(
            "complete_job",
            ctx=job.context,
            resource_type="task",
            resource_id=job.task.task_id,
            execution_status="succeeded",
            details=AuditDetails(
                resource_ids=job.payload["paper_ids"][:50],
                resource_name={
                    "process": "PDF 解析与索引",
                    "evidence": "论文证据提取",
                    "answer": "文献问答",
                    "draft": "报告草稿生成",
                }[job.task.kind],
            ),
        )

    def _commit_index(self, job, index):
        model = index.model
        row = self._one(
            "INSERT INTO embedding_models(model_id, project_id, provider, model_name, revision, dimensions, distance_metric)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (project_id,provider,model_name,revision) DO NOTHING RETURNING model_id",
            (
                uuid4(),
                job.context.project_id,
                model.provider,
                model.model_name,
                model.revision,
                model.dimensions,
                model.distance_metric,
            ),
        )
        saved = self._one(
            "SELECT * FROM embedding_models WHERE project_id=%s AND provider=%s AND model_name=%s AND revision=%s",
            (job.context.project_id, model.provider, model.model_name, model.revision),
        )
        if saved["dimensions"] != model.dimensions or saved["distance_metric"] != model.distance_metric:
            raise AppError(409, "INDEX_MODEL_CONFLICT", "相同模型版本不能变更维度或距离算法")
        model_id = row["model_id"] if row else saved["model_id"]
        for chunk in index.chunks:
            self._db.execute(
                "INSERT INTO chunk_embeddings(chunk_id,model_id,project_id,dimensions,embedding)"
                " VALUES(%s,%s,%s,%s,%s::vector) ON CONFLICT (chunk_id,model_id) DO NOTHING",
                (chunk.chunk_id, model_id, job.context.project_id, model.dimensions, canonical(chunk.vector)),
            )
            saved_vector = json.loads(
                self._one(
                    "SELECT embedding::text AS value FROM chunk_embeddings WHERE chunk_id=%s AND model_id=%s",
                    (chunk.chunk_id, model_id),
                )["value"]
            )
            if any(
                not math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-7)
                for a, b in zip(saved_vector, chunk.vector, strict=True)
            ):
                raise AppError(409, "INDEX_MODEL_CONFLICT", "同一模型版本的已有片段向量不一致")
        self._db.execute(
            "INSERT INTO process_indexes VALUES(%s,%s,%s,%s,%s)",
            (job.task.task_id, job.context.project_id, model_id, model.dimensions, fingerprint(index)),
        )
        self._db.execute(
            "UPDATE project_papers SET index_state='ready' WHERE project_id=%s AND paper_id=%s",
            (job.context.project_id, job.payload["file"].paper_id),
        )

    @operation()
    def indexed_vectors(self, ctx, model, sources):
        from app.indexing import EmbeddingModel

        model = EmbeddingModel.model_validate(model)
        self._scope(ctx)
        self._validate_sources(ctx, sources, [s.paper_id for s in sources])
        vectors = []
        for source in sources:
            row = self._one(
                "SELECT ce.embedding::text AS vector FROM project_papers pp"
                " JOIN process_indexes pi ON pi.task_id=pp.active_process_task_id AND pi.project_id=pp.project_id"
                " JOIN embedding_models m ON m.model_id=pi.model_id AND m.project_id=pi.project_id"
                " JOIN chunk_embeddings ce ON ce.model_id=m.model_id AND ce.project_id=m.project_id"
                " WHERE pp.project_id=%s AND pp.paper_id=%s AND pp.removed_at IS NULL AND pp.index_state='ready'"
                " AND ce.chunk_id=%s AND m.provider=%s AND m.model_name=%s AND m.revision=%s"
                " AND m.dimensions=%s AND m.distance_metric=%s",
                (
                    ctx.project_id,
                    source.paper_id,
                    source.chunk_id,
                    model.provider,
                    model.model_name,
                    model.revision,
                    model.dimensions,
                    model.distance_metric,
                ),
            )
            if row is None:
                raise AppError(409, "INDEX_NOT_READY", "指定模型索引未就绪，请重新处理论文或使用关键词检索")
            vectors.append(json.loads(row["vector"]))
        return vectors

    def _commit_result(self, job, result):
        payload = canonical(result)
        self._db.execute(
            "INSERT INTO task_results(task_id, project_id, kind, committed_attempt, paper_id,"
            " evidence_id, payload, result_fingerprint, created_at)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,now())",
            (
                job.task.task_id,
                job.context.project_id,
                job.task.kind,
                job.task.attempts,
                result.paper_id if job.task.kind in ("process", "evidence") else None,
                result.evidence_id if job.task.kind == "evidence" else None,
                payload,
                fingerprint(result),
            ),
        )
        if job.task.kind == "evidence":
            self._executemany(
                "INSERT INTO evidence_fields(task_id, field_name, project_id, text, status)"
                " VALUES(%s,%s,%s,%s,%s)",
                [
                    (
                        job.task.task_id,
                        name,
                        job.context.project_id,
                        getattr(result, name).text,
                        getattr(result, name).status,
                    )
                    for name in EVIDENCE_FIELDS
                ],
            )
            for name in EVIDENCE_FIELDS:
                for ordinal, source in enumerate(getattr(result, name).sources, start=1):
                    self._insert_result_source(job, source, field_name=name, ordinal=ordinal)
        else:
            for ordinal, source in enumerate(result.sources, start=1):
                self._insert_result_source(job, source, field_name=None, ordinal=ordinal)
        attempt = self._db.execute(
            "UPDATE task_attempts SET status='succeeded', finished_at=now()"
            " WHERE task_id=%s AND attempt_no=%s AND status='processing'"
            " AND lease_expires_at > clock_timestamp()",
            (job.task.task_id, job.task.attempts),
        )
        if attempt.rowcount != 1:
            self._state_conflict()
        task = self._db.execute(
            "UPDATE tasks SET status='succeeded', updated_at=now()"
            " WHERE task_id=%s AND attempts=%s AND status='processing'",
            (job.task.task_id, job.task.attempts),
        )
        if task.rowcount != 1:
            self._state_conflict()
        if job.task.kind == "process":
            self._db.execute(
                "UPDATE project_papers SET status='searchable', index_state='mock_only',"
                " active_process_task_id=%s, updated_at=now()"
                " WHERE project_id=%s AND paper_id=%s",
                (job.task.task_id, job.context.project_id, result.paper_id),
            )

    def _insert_result_source(self, job, source, *, field_name, ordinal):
        self._db.execute(
            "INSERT INTO result_sources(task_id, scope, citation_id, project_id, field_name,"
            " ordinal, paper_id, chunk_id, page_number, excerpt, synthetic)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                job.task.task_id,
                field_name or "root",
                source.citation_id,
                job.context.project_id,
                field_name,
                ordinal,
                source.paper_id,
                source.chunk_id,
                source.page_number,
                source.excerpt,
                source.synthetic,
            ),
        )

    @operation(write=True)
    def fail_job(self, job, code, message):
        self._fail_job(job, code, message)

    def _fail_job(self, job, code, message, *, expired=False):
        stored = self._job_record(job, allow_expired=expired)
        error = TaskError(code=code, message=message)
        if stored.task.status == "failed" and stored.task.error == error:
            return
        if stored.task.status != "processing":
            self._state_conflict()
        attempt = self._db.execute(
            "UPDATE task_attempts SET status='failed', finished_at=now(), error_code=%s, error_message=%s"
            " WHERE task_id=%s AND attempt_no=%s AND status='processing'"
            + (
                " AND lease_expires_at <= clock_timestamp()"
                if expired
                else " AND lease_expires_at > clock_timestamp()"
            ),
            (code, message, job.task.task_id, job.task.attempts),
        )
        if attempt.rowcount != 1:
            self._state_conflict()
        task = self._db.execute(
            "UPDATE tasks SET status='failed', updated_at=now(), error_code=%s, error_message=%s"
            " WHERE task_id=%s AND attempts=%s AND status='processing'",
            (code, message, job.task.task_id, job.task.attempts),
        )
        if task.rowcount != 1:
            self._state_conflict()
        if job.task.kind == "process":
            self._db.execute(
                "UPDATE project_papers SET status='failed', updated_at=now()"
                " WHERE project_id=%s AND paper_id=%s",
                (job.context.project_id, job.payload["file"].paper_id),
            )
        self._outbox(
            "fail_job",
            ctx=job.context,
            resource_type="task",
            resource_id=job.task.task_id,
            execution_status="failed",
            details=AuditDetails(
                resource_ids=job.payload["paper_ids"][:50],
                resource_name={
                    "process": "PDF 解析与索引",
                    "evidence": "论文证据提取",
                    "answer": "文献问答",
                    "draft": "报告草稿生成",
                }[job.task.kind],
            ),
        )

    # ---- reports -----------------------------------------------------------

    def _versions(self, ctx, report_id):
        rows = self._rows(
            "SELECT rv.version_id, rv.project_id, rv.report_id, rv.number, rv.title,"
            " rv.research_question, rv.body, rv.created_at"
            " FROM report_versions rv WHERE rv.report_id=%s AND rv.project_id=%s ORDER BY rv.number",
            (report_id, ctx.project_id),
        )
        if not rows:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        results = []
        for row in rows:
            refs = [
                mapping.source(s)
                for s in self._rows(
                    "SELECT citation_id, paper_id, chunk_id, page_number, excerpt, synthetic"
                    " FROM report_version_sources WHERE project_id=%s AND version_id=%s ORDER BY ordinal",
                    (ctx.project_id, row["version_id"]),
                )
            ]
            results.append(
                ReportVersion(
                    report_id=row["report_id"],
                    version_id=row["version_id"],
                    project_id=row["project_id"],
                    number=row["number"],
                    title=row["title"],
                    research_question=row["research_question"],
                    body=row["body"],
                    sources=refs,
                    created_at=row["created_at"],
                    mock=False,
                )
            )
        return results

    @operation(write=True)
    def save_report(self, ctx, data, key):
        self._scope(ctx)
        validate_key(key)
        digest = fingerprint(data)
        previous = self._replay(ctx, "report-save", key, digest)
        if previous is not None:
            return ReportVersion.model_validate(previous)
        self._validate_sources(ctx, data.sources)
        timestamp = now()
        if data.report_id:
            # Lock the report row: two concurrent saves race on the same base version.
            if (
                self._one(
                    "SELECT 1 FROM reports WHERE report_id=%s AND project_id=%s FOR UPDATE",
                    (data.report_id, ctx.project_id),
                )
                is None
            ):
                raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
            latest = self._one(
                "SELECT version_id, number FROM report_versions WHERE report_id=%s"
                " ORDER BY number DESC LIMIT 1",
                (data.report_id,),
            )
            if latest["version_id"] != data.base_version_id:
                raise AppError(409, "VERSION_CONFLICT", "报告已更新，请读取最新版本后保存")
            report_id, number = data.report_id, latest["number"] + 1
        else:
            if data.base_version_id:
                raise AppError(409, "VERSION_CONFLICT", "新报告不能指定基础版本")
            report_id, number = uuid4(), 1
        version_id = uuid4()
        if data.report_id is None:
            self._db.execute(
                "INSERT INTO reports(report_id, project_id, created_by, current_version_id, created_at)"
                " VALUES(%s,%s,%s,%s,%s)",
                (report_id, ctx.project_id, ctx.user_id, version_id, timestamp),
            )
        self._db.execute(
            "INSERT INTO report_versions(version_id, project_id, report_id, number, base_version_id,"
            " title, research_question, body, created_by, created_at)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                version_id,
                ctx.project_id,
                report_id,
                number,
                data.base_version_id,
                data.title,
                data.research_question,
                data.body,
                ctx.user_id,
                timestamp,
            ),
        )
        self._executemany(
            "INSERT INTO report_version_sources(version_id, citation_id, project_id, ordinal,"
            " paper_id, chunk_id, page_number, excerpt, synthetic)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [
                (
                    version_id,
                    s.citation_id,
                    ctx.project_id,
                    ordinal,
                    s.paper_id,
                    s.chunk_id,
                    s.page_number,
                    s.excerpt,
                    s.synthetic,
                )
                for ordinal, s in enumerate(data.sources, start=1)
            ],
        )
        if data.report_id is not None:
            self._db.execute(
                "UPDATE reports SET current_version_id=%s WHERE report_id=%s",
                (version_id, report_id),
            )
        # Read back through the version chain so the returned object is a detached copy.
        result = self._versions(ctx, report_id)[-1]
        self._remember(ctx, "report-save", key, digest, result.model_dump(mode="json"), version_id=version_id)
        self._outbox("save_report", ctx=ctx, resource_type="report", resource_id=report_id)
        return result

    @operation()
    def versions(self, ctx, report_id):
        self._scope(ctx)
        return self._versions(ctx, report_id)

    @operation()
    def reports(self, ctx):
        self._scope(ctx)
        ids = self._rows(
            "SELECT report_id FROM reports WHERE project_id=%s ORDER BY created_at, report_id",
            (ctx.project_id,),
        )
        return [self._versions(ctx, row["report_id"])[-1] for row in ids]

    @operation()
    def controlled_statistics(self, ctx, intent):
        self._scope(ctx)
        if intent == "by_year":
            rows = self._rows(
                "SELECT p.year, count(*) AS count FROM project_papers pp"
                " JOIN papers p ON p.metadata_id=pp.metadata_id"
                " WHERE pp.project_id=%s AND pp.removed_at IS NULL GROUP BY p.year ORDER BY p.year",
                (ctx.project_id,),
            )
            return ["year", "count"], [dict(row) for row in rows]
        if intent == "by_topic":
            rows = self._rows(
                "SELECT t.name AS topic, count(DISTINCT pp.paper_id) AS count"
                " FROM project_papers pp"
                " JOIN paper_topics pt ON pt.project_id=pp.project_id AND pt.paper_id=pp.paper_id"
                " JOIN topics t ON t.topic_id=pt.topic_id"
                " WHERE pp.project_id=%s AND pp.removed_at IS NULL"
                " GROUP BY t.topic_id, t.name ORDER BY t.name",
                (ctx.project_id,),
            )
            return ["topic", "count"], [dict(row) for row in rows]
        if intent == "missing_evidence":
            rows = self._rows(
                "SELECT pp.paper_id::text AS paper_id, p.title FROM project_papers pp"
                " JOIN papers p ON p.metadata_id=pp.metadata_id"
                " WHERE pp.project_id=%s AND pp.removed_at IS NULL AND NOT EXISTS ("
                "  SELECT 1 FROM task_results tr WHERE tr.project_id=pp.project_id"
                "  AND tr.paper_id=pp.paper_id AND tr.kind='evidence')"
                " ORDER BY pp.created_at, pp.paper_id",
                (ctx.project_id,),
            )
            return ["paper_id", "title"], [dict(row) for row in rows]
        if intent == "overview":
            # A-side extension (docs/member-a-overview-stats.md): one project-scoped
            # metric/value summary for the project home area. Additive beyond the
            # frozen AB-1 intent set; HTTP exposure awaits B/D review.
            row = self._one(
                """
                SELECT
                  (SELECT count(*) FROM project_papers pp
                    WHERE pp.project_id=%s AND pp.removed_at IS NULL) AS papers_total,
                  (SELECT count(*) FROM project_papers pp
                    WHERE pp.project_id=%s AND pp.removed_at IS NULL AND pp.status='searchable')
                    AS papers_searchable,
                  (SELECT count(*) FROM project_papers pp
                    WHERE pp.project_id=%s AND pp.removed_at IS NULL AND pp.status='processing')
                    AS papers_processing,
                  (SELECT count(*) FROM project_papers pp
                    WHERE pp.project_id=%s AND pp.removed_at IS NULL AND pp.status='failed')
                    AS papers_failed,
                  (SELECT count(*) FROM project_papers pp
                    WHERE pp.project_id=%s AND pp.removed_at IS NULL AND pp.status='pending')
                    AS papers_pending,
                  (SELECT count(*) FROM chunks c
                    WHERE c.project_id=%s AND EXISTS (
                      SELECT 1 FROM project_papers pp WHERE pp.project_id=c.project_id
                      AND pp.paper_id=c.paper_id AND pp.removed_at IS NULL)) AS chunks_total,
                  (SELECT count(DISTINCT tr.paper_id) FROM task_results tr
                    WHERE tr.project_id=%s AND tr.kind='evidence' AND EXISTS (
                      SELECT 1 FROM project_papers pp WHERE pp.project_id=tr.project_id
                      AND pp.paper_id=tr.paper_id AND pp.removed_at IS NULL))
                    AS papers_with_evidence,
                  (SELECT count(*) FROM reports r WHERE r.project_id=%s) AS reports,
                  (SELECT count(*) FROM report_versions rv WHERE rv.project_id=%s)
                    AS report_versions,
                  (SELECT count(*) FROM project_members m WHERE m.project_id=%s)
                    AS members_total
                """,
                (ctx.project_id,) * 10,
            )
            metrics = [
                "papers_total",
                "papers_searchable",
                "papers_processing",
                "papers_failed",
                "papers_pending",
                "chunks_total",
                "papers_with_evidence",
                "reports",
                "report_versions",
                "members_total",
            ]
            return ["metric", "value"], [{"metric": name, "value": int(row[name])} for name in metrics]
        raise AppError(400, "UNSUPPORTED_INTENT", "不支持的统计意图")

    # ---- audit ---------------------------------------------------------------

    @operation(write=True)
    def append_audit(self, event):
        if event.timestamp.tzinfo is None:
            raise invalid()
        digest = fingerprint(event)
        existing = self._one(
            "SELECT paperpilot_audit.event_fingerprint(%s) AS event_fingerprint", (event.event_id,)
        )
        if existing and existing["event_fingerprint"] is not None:
            if existing["event_fingerprint"] == digest:
                return
            raise AppError(409, "AUDIT_EVENT_CONFLICT", "审计事件编号已使用")
        if (
            event.project_id is not None
            and self._one("SELECT 1 FROM projects WHERE project_id=%s", (event.project_id,)) is None
        ) or (
            event.actor_id is not None
            and self._one("SELECT 1 FROM users WHERE user_id=%s", (event.actor_id,)) is None
        ):
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        self._db.execute(
            "INSERT INTO audit_events(event_id, timestamp, request_id, actor_id, project_id,"
            " action, resource_type, decision, execution_status, policy_id, duration_ms, mock,"
            " event_fingerprint, details) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (
                event.event_id,
                event.timestamp,
                event.request_id,
                event.actor_id,
                event.project_id,
                event.action,
                event.resource_type,
                event.decision,
                event.execution_status,
                event.policy_id,
                event.duration_ms,
                event.mock,
                digest,
                event.details.model_dump_json() if event.details is not None else None,
            ),
        )

    @operation()
    def audit(self, ctx, *, action, decision, actor_id, since, until, offset, limit):
        self._scope(ctx, owner=True)
        if (
            offset < 0
            or not 1 <= limit <= 100
            or (since and since.tzinfo is None)
            or (until and until.tzinfo is None)
            or (since and until and since > until)
        ):
            raise invalid()
        where = (
            " FROM paperpilot_audit.project_events WHERE project_id=%s"
            " AND (%s::text IS NULL OR action=%s)"
            " AND (%s::text IS NULL OR decision=%s)"
            " AND (%s::uuid IS NULL OR actor_id=%s)"
            " AND (%s::timestamptz IS NULL OR timestamp >= %s)"
            " AND (%s::timestamptz IS NULL OR timestamp <= %s)"
        )
        values = (
            ctx.project_id,
            action,
            action,
            decision,
            decision,
            actor_id,
            actor_id,
            since,
            since,
            until,
            until,
        )
        total = self._one("SELECT count(*) AS total" + where, values)["total"]
        rows = self._rows(
            "SELECT event_id, timestamp, request_id, actor_id, project_id, action, resource_type,"
            " decision, execution_status, policy_id, duration_ms, mock, details"
            + where
            + " ORDER BY timestamp DESC, event_id DESC LIMIT %s OFFSET %s",
            (*values, limit, offset),
        )
        return AuditList(
            items=[
                AuditEvent(
                    event_id=row["event_id"],
                    timestamp=row["timestamp"],
                    request_id=row["request_id"],
                    actor_id=row["actor_id"],
                    project_id=row["project_id"],
                    action=row["action"],
                    resource_type=row["resource_type"],
                    decision=row["decision"],
                    policy_id=row["policy_id"],
                    execution_status=row["execution_status"],
                    duration_ms=row["duration_ms"],
                    mock=row["mock"],
                    details=row["details"],
                )
                for row in rows
            ],
            total=total,
            offset=offset,
            limit=limit,
            mock=False,
        )
