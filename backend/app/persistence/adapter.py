"""AB-1 implementation using SQLite transactions, not a persisted Mock snapshot."""

import json
import re
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from functools import wraps
from inspect import signature
from uuid import UUID, uuid4

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
from app.errors import AppError
from app.persistence import models
from app.schemas import (
    AuditEvent,
    AuditList,
    EvidenceResult,
    Member,
    Project,
    ReportVersion,
    ResultReference,
    TaskError,
)


def now():
    return datetime.now(UTC)


def operation(write=False):
    def decorate(method):
        parameters = signature(method)

        @wraps(method)
        def invoke(self, *args, **kwargs):
            with self.database.transaction(write=write) as conn:
                token = self._connection.set(conn)
                try:
                    changes = conn.total_changes
                    result = method(self, *args, **kwargs)
                    if write and conn.total_changes != changes:
                        arguments = parameters.bind(self, *args, **kwargs).arguments
                        self._journal(method.__name__, arguments, result)
                    if write and self._before_commit is not None:
                        self._before_commit(method.__name__, conn)
                    return result
                finally:
                    self._connection.reset(token)

        return invoke

    return decorate


class SQLiteData:
    def __init__(self, database, *, lease_seconds=120, before_commit=None):
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("Invalid lease duration")
        self.database = database
        self.lease_seconds = lease_seconds
        self._before_commit = before_commit
        self._connection = ContextVar(f"sqlite_connection_{id(self)}")

    @property
    def _db(self):
        return self._connection.get()

    def _one(self, sql, values=()):
        return self._db.execute(sql, values).fetchone()

    def _role(self, user_id, project_id):
        row = self._one(
            "SELECT role FROM project_members WHERE project_id=? AND user_id=?",
            (str(project_id), str(user_id)),
        )
        return row[0] if row else None

    def _scope(self, ctx, owner=False):
        role = self._role(ctx.user_id, ctx.project_id)
        if role is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        if owner and role != "owner":
            raise AppError(403, "OWNER_REQUIRED", "此操作需要项目负责人权限")

    def _paper(self, ctx, paper_id):
        row = self._one(
            "SELECT p.*,pp.project_id,pp.status FROM papers p JOIN project_papers pp USING(paper_id) WHERE pp.project_id=? AND p.paper_id=? AND pp.removed=0",
            (str(ctx.project_id), str(paper_id)),
        )
        if row is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        return models.paper(row)

    def _sources(self, ctx, paper_id):
        return [
            models.source(row)
            for row in self._db.execute(
                "SELECT * FROM sources WHERE project_id=? AND paper_id=? ORDER BY page_number,chunk_id",
                (str(ctx.project_id), str(paper_id)),
            )
        ]

    def _journal(self, name, arguments, result):
        first = arguments.get("ctx", arguments.get("job", arguments.get("event")))
        ctx = first.context if isinstance(first, Job) else first
        actor = project = request = resource = None
        if isinstance(ctx, Context):
            actor, project, request = ctx.user_id, ctx.project_id, ctx.request_id
        elif isinstance(first, AuditEvent):
            actor, project, request = first.actor_id, first.project_id, first.request_id
            resource = first.event_id
        elif name == "create_project":
            actor, project = result.owner_id, result.project_id
            request = arguments["request_id"]
        elif isinstance(result, Job):
            actor, project, request = (
                result.context.user_id,
                result.context.project_id,
                result.context.request_id,
            )
            resource = result.task.task_id
        if isinstance(first, Job):
            resource = first.task.task_id
        elif name == "import_paper":
            resource = result[0].paper_id
        elif result is not None:
            resource = next(
                (getattr(result, n) for n in ("version_id", "task_id", "project_id") if hasattr(result, n)),
                resource,
            )
        self._db.execute(
            "INSERT INTO domain_events VALUES(?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                now().isoformat(),
                str(request) if request else None,
                str(actor) if actor else None,
                str(project) if project else None,
                name,
                str(resource) if resource else None,
            ),
        )

    def _replay(self, ctx, operation_name, key, digest):
        row = self._one(
            "SELECT fingerprint,response_json FROM idempotency WHERE user_id=? AND project_id=? AND operation=? AND key=?",
            (str(ctx.user_id), str(ctx.project_id), operation_name, key),
        )
        if row and row["fingerprint"] != digest:
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同请求")
        return json.loads(row["response_json"]) if row else None

    def _remember(self, ctx, name, key, digest, response):
        self._db.execute(
            "INSERT INTO idempotency VALUES(?,?,?,?,?,?)",
            (str(ctx.user_id), str(ctx.project_id), name, key, digest, canonical(response)),
        )

    def _task(self, ctx, task_id):
        row = self._one(
            "SELECT * FROM tasks WHERE task_id=? AND project_id=?", (str(task_id), str(ctx.project_id))
        )
        if row is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        job = models.job(row)
        for paper_id in job.payload["paper_ids"]:
            self._paper(ctx, paper_id)
        return job

    def _create_task(self, ctx, kind, payload, key):
        validate_key(key)
        payload = normalize_payload(ctx, kind, payload)
        for paper_id in payload["paper_ids"]:
            self._paper(ctx, paper_id)
        digest = fingerprint(payload)
        previous = self._replay(ctx, kind, key, digest)
        if previous is not None:
            from app.schemas import Task

            return Task.model_validate(previous)
        task_id, timestamp = str(uuid4()), now().isoformat()
        self._db.execute(
            "INSERT INTO tasks(task_id,project_id,actor_id,request_id,kind,status,attempts,created_at,updated_at,payload_json) VALUES(?,?,?,?,?,'pending',0,?,?,?)",
            (
                task_id,
                str(ctx.project_id),
                str(ctx.user_id),
                str(ctx.request_id),
                kind,
                timestamp,
                timestamp,
                canonical(payload),
            ),
        )
        for paper_id in payload["paper_ids"]:
            self._db.execute(
                "INSERT INTO task_papers VALUES(?,?,?)", (task_id, str(ctx.project_id), str(paper_id))
            )
        result = models.task(self._one("SELECT * FROM tasks WHERE task_id=?", (task_id,)))
        self._remember(ctx, kind, key, digest, result)
        return result

    def _validate_sources(self, ctx, sources, allowed_ids=None):
        if len({s.citation_id for s in sources}) != len(sources):
            raise AppError(502, "INVALID_CITATION", "来源编号重复")
        for source in sources:
            self._paper(ctx, source.paper_id)
            if allowed_ids is not None and source.paper_id not in allowed_ids:
                raise AppError(502, "INVALID_CITATION", "来源超出任务范围")
            row = self._one(
                "SELECT * FROM sources WHERE chunk_id=? AND project_id=? AND paper_id=?",
                (str(source.chunk_id), str(ctx.project_id), str(source.paper_id)),
            )
            if row is None or (row["page_number"], row["excerpt"], bool(row["synthetic"])) != (
                source.page_number,
                source.excerpt,
                source.synthetic,
            ):
                raise AppError(502, "INVALID_CITATION", "来源与授权片段不一致")

    def _job_record(self, job):
        row = self._one("SELECT * FROM tasks WHERE task_id=?", (str(job.task.task_id),))
        if row is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        stored = models.job(row)
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
        return stored

    @staticmethod
    def _state_conflict():
        raise AppError(409, "TASK_STATE_CONFLICT", "任务执行次数或状态已变化")

    @operation()
    def user_by_name(self, username):
        return models.user(self._one("SELECT * FROM users WHERE username=?", (username,)))

    @operation()
    def user_by_id(self, user_id):
        return models.user(self._one("SELECT * FROM users WHERE user_id=?", (str(user_id),)))

    @operation()
    def role(self, user_id, project_id):
        return self._role(user_id, project_id)

    @operation()
    def projects(self, user_id):
        if self._one("SELECT 1 FROM users WHERE user_id=?", (str(user_id),)) is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        return [
            Project(**dict(row), mock=False)
            for row in self._db.execute(
                "SELECT p.*,m.role FROM projects p JOIN project_members m USING(project_id) WHERE m.user_id=? ORDER BY p.rowid",
                (str(user_id),),
            )
        ]

    @operation(write=True)
    def create_project(self, user_id, data, request_id):
        if self._one("SELECT 1 FROM users WHERE user_id=?", (str(user_id),)) is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        project_id = str(uuid4())
        self._db.execute(
            "INSERT INTO projects VALUES(?,?,?,?)", (project_id, data.name, data.description, str(user_id))
        )
        self._db.execute("INSERT INTO project_members VALUES(?,?,'owner')", (project_id, str(user_id)))
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
            Member(user=models.user(row), role=row["role"])
            for row in self._db.execute(
                "SELECT u.*,m.role FROM project_members m JOIN users u USING(user_id) WHERE project_id=? ORDER BY u.username",
                (str(ctx.project_id),),
            )
        ]

    @operation(write=True)
    def add_member(self, ctx, username):
        self._scope(ctx, owner=True)
        user = models.user(self._one("SELECT * FROM users WHERE username=?", (username,)))
        if user is None:
            raise AppError(404, "USER_NOT_FOUND", "账户不存在")
        # A repeated insert retains the existing owner/member role.
        if self._role(user.user_id, ctx.project_id) is None:
            self._db.execute(
                "INSERT INTO project_members VALUES(?,?,'member')", (str(ctx.project_id), str(user.user_id))
            )
        return Member(user=user, role=self._role(user.user_id, ctx.project_id))

    @operation(write=True)
    def remove_member(self, ctx, user_id):
        self._scope(ctx, owner=True)
        if self._role(user_id, ctx.project_id) == "owner":
            raise AppError(409, "OWNER_IMMUTABLE", "不能移除项目负责人")
        self._db.execute(
            "DELETE FROM project_members WHERE project_id=? AND user_id=?",
            (str(ctx.project_id), str(user_id)),
        )

    @operation()
    def papers(self, ctx, year_from=None, year_to=None):
        self._scope(ctx)
        if any(
            v is not None and (not isinstance(v, int) or not 1000 <= v <= 3000) for v in (year_from, year_to)
        ) or (year_from is not None and year_to is not None and year_from > year_to):
            raise invalid()
        return [
            models.paper(row)
            for row in self._db.execute(
                "SELECT p.*,pp.project_id,pp.status FROM papers p JOIN project_papers pp USING(paper_id) WHERE pp.project_id=? AND pp.removed=0 AND (? IS NULL OR p.year>=?) AND (? IS NULL OR p.year<=?) ORDER BY p.rowid",
                (str(ctx.project_id), year_from, year_from, year_to, year_to),
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
        return self._sources(ctx, paper_id)

    @operation()
    def evidence(self, ctx, paper_id):
        self._scope(ctx)
        self._paper(ctx, paper_id)
        return [
            EvidenceResult.model_validate_json(row[0])
            for row in self._db.execute(
                "SELECT result_json FROM evidence WHERE project_id=? AND paper_id=? ORDER BY rowid",
                (str(ctx.project_id), str(paper_id)),
            )
        ]

    @operation(write=True)
    def remove_paper(self, ctx, paper_id):
        self._scope(ctx, owner=True)
        self._paper(ctx, paper_id)
        self._db.execute(
            "UPDATE project_papers SET removed=1 WHERE project_id=? AND paper_id=?",
            (str(ctx.project_id), str(paper_id)),
        )

    @operation(write=True)
    def import_paper(self, ctx, title, year, digest, file, key):
        self._scope(ctx)
        validate_file(ctx, file)
        validate_key(key)
        if (
            not isinstance(title, str)
            or not 1 <= len(title) <= 200
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or (year is not None and (not isinstance(year, int) or not 1000 <= year <= 3000))
        ):
            raise invalid()
        request_digest = f"{digest}:{title}:{year}"
        previous = self._replay(ctx, "upload", key, request_digest)
        if previous is not None:
            from app.schemas import Paper, Task

            self._paper(ctx, UUID(previous[0]["paper_id"]))
            return Paper.model_validate(previous[0]), Task.model_validate(previous[1])
        if self._one(
            "SELECT 1 FROM project_papers WHERE project_id=? AND digest=?", (str(ctx.project_id), digest)
        ):
            raise AppError(409, "DUPLICATE_PAPER", "当前项目已导入同一文件")
        if self._one("SELECT 1 FROM papers WHERE paper_id=?", (str(file.paper_id),)):
            raise AppError(409, "IDENTIFIER_CONFLICT", "资源编号已使用")
        self._db.execute("INSERT INTO papers VALUES(?,?,?)", (str(file.paper_id), title, year))
        self._db.execute(
            "INSERT INTO project_papers(project_id,paper_id,digest,storage_key,status) VALUES(?,?,?,?,'pending')",
            (str(ctx.project_id), str(file.paper_id), digest, file.storage_key),
        )
        task = self._create_task(ctx, "process", {"file": file, "paper_ids": [file.paper_id]}, key)
        result = self._paper(ctx, file.paper_id), task
        self._remember(ctx, "upload", key, request_digest, result)
        return result

    @operation(write=True)
    def create_task(self, ctx, kind, payload, key):
        self._scope(ctx)
        return self._create_task(ctx, kind, payload, key)

    @operation()
    def task(self, ctx, task_id):
        self._scope(ctx)
        return self._task(ctx, task_id).task

    @operation(write=True)
    def retry_task(self, ctx, task_id):
        self._scope(ctx)
        job = self._task(ctx, task_id)
        if job.task.status != "failed":
            raise AppError(409, "TASK_NOT_RETRYABLE", "仅失败任务可以重试")
        self._db.execute(
            "UPDATE tasks SET status='pending',actor_id=?,request_id=?,updated_at=?,error_json=NULL,result_json=NULL,result_ref_json=NULL,lease_until=NULL WHERE task_id=? AND status='failed'",
            (str(ctx.user_id), str(ctx.request_id), now().isoformat(), str(task_id)),
        )
        if job.task.kind == "process":
            self._db.execute(
                "UPDATE project_papers SET status='pending' WHERE project_id=? AND paper_id=?",
                (str(ctx.project_id), str(job.payload["file"].paper_id)),
            )
        return models.task(self._one("SELECT * FROM tasks WHERE task_id=?", (str(task_id),)))

    @operation(write=True)
    def claim_job(self):
        timestamp = now()
        row = self._one(
            "SELECT * FROM tasks WHERE status='pending' OR (status='processing' AND lease_until<=?) ORDER BY created_at,task_id LIMIT 1",
            (timestamp.isoformat(),),
        )
        if row is None:
            return None
        cursor = self._db.execute(
            "UPDATE tasks SET status='processing',attempts=attempts+1,updated_at=?,lease_until=? WHERE task_id=? AND attempts=? AND (status='pending' OR (status='processing' AND lease_until<=?))",
            (
                timestamp.isoformat(),
                (timestamp + timedelta(seconds=self.lease_seconds)).isoformat(),
                row["task_id"],
                row["attempts"],
                timestamp.isoformat(),
            ),
        )
        if cursor.rowcount != 1:
            self._state_conflict()
        job = models.job(self._one("SELECT * FROM tasks WHERE task_id=?", (row["task_id"],)))
        if job.task.kind == "process":
            self._db.execute(
                "UPDATE project_papers SET status='processing' WHERE project_id=? AND paper_id=?",
                (str(job.context.project_id), str(job.payload["file"].paper_id)),
            )
        return job

    @operation(write=True)
    def complete_job(self, job, result):
        stored = self._job_record(job)
        self._scope(job.context)
        for pid in job.payload["paper_ids"]:
            self._paper(job.context, pid)
        result = normalize_result(job.task.kind, result)
        if stored.task.status == "succeeded" and fingerprint(stored.task.result) == fingerprint(result):
            return
        if stored.task.status != "processing":
            self._state_conflict()
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
                    "INSERT INTO sources VALUES(?,?,?,?,?,?,?)",
                    (
                        str(source.chunk_id),
                        str(job.context.project_id),
                        str(result.paper_id),
                        source.citation_id,
                        source.page_number,
                        source.excerpt,
                        int(source.synthetic),
                    ),
                )
            self._db.execute(
                "UPDATE project_papers SET status='searchable' WHERE project_id=? AND paper_id=?",
                (str(job.context.project_id), str(result.paper_id)),
            )
        elif job.task.kind == "evidence":
            if result.paper_id != job.payload["paper_ids"][0]:
                raise AppError(502, "ADAPTER_CONTRACT_ERROR", "证据论文与任务不一致")
            references = set()
            for name in ("research_question", "method", "results", "conclusion", "limitations"):
                field = getattr(result, name)
                self._validate_sources(job.context, field.sources, [result.paper_id])
                if field.status == "pending_verification" and (not field.text or not field.sources):
                    raise AppError(502, "INVALID_CITATION", "证据缺少来源")
                references.update(s.chunk_id for s in field.sources)
            self._db.execute(
                "INSERT INTO evidence VALUES(?,?,?,?,?)",
                (
                    str(result.evidence_id),
                    str(job.task.task_id),
                    str(job.context.project_id),
                    str(result.paper_id),
                    result.model_dump_json(),
                ),
            )
            for chunk_id in references:
                self._db.execute(
                    "INSERT INTO evidence_sources VALUES(?,?,?,?)",
                    (
                        str(result.evidence_id),
                        str(chunk_id),
                        str(job.context.project_id),
                        str(result.paper_id),
                    ),
                )
        else:
            self._validate_sources(job.context, result.sources, job.payload["paper_ids"])
            if job.task.kind == "answer" and (
                len(set(result.citation_ids)) != len(result.citation_ids)
                or set(result.citation_ids) != {s.citation_id for s in result.sources}
                or (not result.insufficient_evidence and not result.sources)
            ):
                raise AppError(502, "INVALID_CITATION", "回答缺少有效引用")
        resource_id = (
            result.paper_id
            if job.task.kind == "process"
            else (result.evidence_id if job.task.kind == "evidence" else job.task.task_id)
        )
        reference = ResultReference(
            resource_type="paper" if job.task.kind == "process" else job.task.kind, resource_id=resource_id
        )
        cursor = self._db.execute(
            "UPDATE tasks SET status='succeeded',updated_at=?,result_json=?,result_ref_json=?,lease_until=NULL WHERE task_id=? AND attempts=? AND status='processing'",
            (
                now().isoformat(),
                result.model_dump_json(),
                reference.model_dump_json(),
                str(job.task.task_id),
                job.task.attempts,
            ),
        )
        if cursor.rowcount != 1:
            self._state_conflict()

    @operation(write=True)
    def fail_job(self, job, code, message):
        stored = self._job_record(job)
        error = TaskError(code=code, message=message)
        if stored.task.status == "failed" and stored.task.error == error:
            return
        if stored.task.status != "processing":
            self._state_conflict()
        cursor = self._db.execute(
            "UPDATE tasks SET status='failed',updated_at=?,error_json=?,result_json=NULL,result_ref_json=NULL,lease_until=NULL WHERE task_id=? AND attempts=? AND status='processing'",
            (now().isoformat(), error.model_dump_json(), str(job.task.task_id), job.task.attempts),
        )
        if cursor.rowcount != 1:
            self._state_conflict()
        if job.task.kind == "process":
            self._db.execute(
                "UPDATE project_papers SET status='failed' WHERE project_id=? AND paper_id=?",
                (str(job.context.project_id), str(job.payload["file"].paper_id)),
            )

    def _versions(self, ctx, report_id):
        rows = self._db.execute(
            "SELECT * FROM report_versions WHERE report_id=? AND project_id=? ORDER BY number",
            (str(report_id), str(ctx.project_id)),
        ).fetchall()
        if not rows:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        results = []
        for row in rows:
            refs = [
                models.source(s)
                for s in self._db.execute(
                    "SELECT * FROM report_sources WHERE version_id=? ORDER BY rowid", (row["version_id"],)
                )
            ]
            results.append(ReportVersion(**dict(row), sources=refs, mock=False))
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
        if data.report_id:
            old = self._versions(ctx, data.report_id)
            if old[-1].version_id != data.base_version_id:
                raise AppError(409, "VERSION_CONFLICT", "报告已更新，请读取最新版本后保存")
            report_id, number = str(data.report_id), old[-1].number + 1
        else:
            if data.base_version_id:
                raise AppError(409, "VERSION_CONFLICT", "新报告不能指定基础版本")
            report_id, number = str(uuid4()), 1
            self._db.execute("INSERT INTO reports VALUES(?,?)", (report_id, str(ctx.project_id)))
        version_id, timestamp = str(uuid4()), now().isoformat()
        self._db.execute(
            "INSERT INTO report_versions VALUES(?,?,?,?,?,?,?,?)",
            (
                version_id,
                report_id,
                str(ctx.project_id),
                number,
                data.title,
                data.research_question,
                data.body,
                timestamp,
            ),
        )
        for s in data.sources:
            self._db.execute(
                "INSERT INTO report_sources VALUES(?,?,?,?,?,?,?,?)",
                (
                    version_id,
                    str(ctx.project_id),
                    s.citation_id,
                    str(s.paper_id),
                    str(s.chunk_id),
                    s.page_number,
                    s.excerpt,
                    int(s.synthetic),
                ),
            )
        result = self._versions(ctx, UUID(report_id))[-1]
        self._remember(ctx, "report-save", key, digest, result)
        return result

    @operation()
    def versions(self, ctx, report_id):
        self._scope(ctx)
        return self._versions(ctx, report_id)

    @operation()
    def reports(self, ctx):
        self._scope(ctx)
        ids = self._db.execute(
            "SELECT report_id FROM reports WHERE project_id=? ORDER BY rowid", (str(ctx.project_id),)
        ).fetchall()
        return [self._versions(ctx, UUID(row[0]))[-1] for row in ids]

    @operation()
    def controlled_statistics(self, ctx, intent):
        self._scope(ctx)
        if intent == "by_year":
            rows = self._db.execute(
                "SELECT p.year,COUNT(*) AS count FROM project_papers pp JOIN papers p USING(paper_id) WHERE pp.project_id=? AND pp.removed=0 GROUP BY p.year ORDER BY p.year",
                (str(ctx.project_id),),
            )
            return ["year", "count"], [dict(row) for row in rows]
        if intent == "by_topic":
            rows = self._db.execute(
                "SELECT t.name AS topic,COUNT(DISTINCT pp.paper_id) AS count FROM project_papers pp JOIN paper_topics pt ON pt.project_id=pp.project_id AND pt.paper_id=pp.paper_id JOIN topics t USING(topic_id) WHERE pp.project_id=? AND pp.removed=0 GROUP BY t.topic_id ORDER BY t.name",
                (str(ctx.project_id),),
            )
            return ["topic", "count"], [dict(row) for row in rows]
        if intent == "missing_evidence":
            rows = self._db.execute(
                "SELECT p.paper_id,p.title FROM project_papers pp JOIN papers p USING(paper_id) WHERE pp.project_id=? AND pp.removed=0 AND NOT EXISTS(SELECT 1 FROM evidence e WHERE e.project_id=pp.project_id AND e.paper_id=pp.paper_id)",
                (str(ctx.project_id),),
            )
            return ["paper_id", "title"], [dict(row) for row in rows]
        raise AppError(400, "UNSUPPORTED_INTENT", "不支持的统计意图")

    @operation(write=True)
    def append_audit(self, event):
        if event.timestamp.tzinfo is None:
            raise invalid()
        existing = self._one("SELECT event_json FROM audit_events WHERE event_id=?", (str(event.event_id),))
        if existing:
            if AuditEvent.model_validate_json(existing[0]) == event:
                return
            raise AppError(409, "AUDIT_EVENT_CONFLICT", "审计事件编号已使用")
        if (
            event.project_id is not None
            and self._one("SELECT 1 FROM projects WHERE project_id=?", (str(event.project_id),)) is None
        ) or (
            event.actor_id is not None
            and self._one("SELECT 1 FROM users WHERE user_id=?", (str(event.actor_id),)) is None
        ):
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        self._db.execute(
            "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",
            (
                str(event.event_id),
                event.timestamp.astimezone(UTC).isoformat(),
                str(event.request_id),
                str(event.actor_id) if event.actor_id else None,
                str(event.project_id) if event.project_id else None,
                event.action,
                event.decision,
                event.model_dump_json(),
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
        sql = " FROM audit_events WHERE project_id=? AND (? IS NULL OR action=?) AND (? IS NULL OR decision=?) AND (? IS NULL OR actor_id=?) AND (? IS NULL OR timestamp>=?) AND (? IS NULL OR timestamp<=?)"
        actor = str(actor_id) if actor_id else None
        start = since.astimezone(UTC).isoformat() if since else None
        end = until.astimezone(UTC).isoformat() if until else None
        values = (
            str(ctx.project_id),
            action,
            action,
            decision,
            decision,
            actor,
            actor,
            start,
            start,
            end,
            end,
        )
        total = self._one("SELECT COUNT(*)" + sql, values)[0]
        rows = self._db.execute(
            "SELECT event_json" + sql + " ORDER BY timestamp DESC,event_id DESC LIMIT ? OFFSET ?",
            (*values, limit, offset),
        )
        return AuditList(
            items=[AuditEvent.model_validate_json(row[0]) for row in rows],
            total=total,
            offset=offset,
            limit=limit,
            mock=False,
        )
