"""Synthetic development store. No durable storage or real database security claims."""

from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from functools import wraps
import re
from threading import RLock
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.adapters.data_rules import (
    fingerprint,
    invalid,
    normalize_payload,
    normalize_result,
    validate_file,
    validate_key,
)
from app.adapters.ports import FileReference, Job
from app.errors import AppError
from app.schemas import (
    AuditEvent,
    AuditList,
    Member,
    Paper,
    Project,
    ProjectCreate,
    ReportSave,
    ReportVersion,
    ResultReference,
    Source,
    Task,
    TaskError,
    TaskStatus,
    User,
)


def seed_id(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"paperpilot/mock/{name}")


def now():
    return datetime.now(UTC)


def read(method):
    @wraps(method)
    def detached(self, *args, **kwargs):
        with self.lock:
            return deepcopy(method(self, *args, **kwargs))

    return detached


def write(operation):
    def decorate(method):
        @wraps(method)
        def atomic(self, *args, **kwargs):
            with self._transaction(operation):
                result = method(self, *args, **kwargs)
            return deepcopy(result)

        return atomic

    return decorate


class MockData:
    def __init__(self, *, before_commit=None):
        self.lock = RLock()
        self._before_commit = before_commit
        self.users = {
            name: User(user_id=seed_id(name), username=name, display_name=name.title())
            for name in ("alice", "bob", "eve")
        }
        alice, bob, eve = (self.users[n].user_id for n in ("alice", "bob", "eve"))
        self.project_records = {
            seed_id("project-a"): Project(
                project_id=seed_id("project-a"),
                name="文献研究演示项目",
                description="合成数据，仅用于契约演示",
                owner_id=alice,
                role="owner",
            ),
            seed_id("project-b"): Project(
                project_id=seed_id("project-b"),
                name="另一个隔离项目",
                description="仅 Eve 可见",
                owner_id=eve,
                role="owner",
            ),
        }
        self.memberships = {
            (seed_id("project-a"), alice): "owner",
            (seed_id("project-a"), bob): "member",
            (seed_id("project-b"), eve): "owner",
        }
        self.paper_records: dict[UUID, Paper] = {}
        self.source_records: dict[UUID, list[Source]] = {}
        self.evidence_records: dict[UUID, list] = {}
        self.removed: set[UUID] = set()
        self.digests: dict[tuple[UUID, str], UUID] = {}
        self.jobs: dict[UUID, Job] = {}
        self.report_records: dict[UUID, list[ReportVersion]] = {}
        self.idempotency: dict[tuple, tuple[str, object]] = {}
        self.events: list[AuditEvent] = []
        for suffix, title in (("a", "合成示例：检索增强生成的证据核验"), ("b", "隔离项目合成论文")):
            pid, paper_id = seed_id(f"project-{suffix}"), seed_id(f"paper-{suffix}")
            self.paper_records[paper_id] = Paper(
                paper_id=paper_id, project_id=pid, title=title, year=2024, status="searchable"
            )
            self.source_records[paper_id] = [
                Source(
                    citation_id="1",
                    paper_id=paper_id,
                    chunk_id=seed_id(f"chunk-{suffix}"),
                    page_number=1,
                    excerpt="【合成片段】本示例展示检索增强生成的来源定位，不代表真实论文或实验结论。",
                )
            ]

    @contextmanager
    def _transaction(self, operation):
        # Small in-memory test store only; this is not evidence of database transaction behavior.
        fields = (
            "users",
            "project_records",
            "memberships",
            "paper_records",
            "source_records",
            "evidence_records",
            "removed",
            "digests",
            "jobs",
            "report_records",
            "idempotency",
            "events",
        )
        with self.lock:
            snapshot = {name: deepcopy(getattr(self, name)) for name in fields}
            try:
                yield
                if self._before_commit is not None:
                    self._before_commit(operation)
            except Exception:
                for name, value in snapshot.items():
                    setattr(self, name, value)
                raise

    @read
    def user_by_name(self, username):
        return self.users.get(username)

    @read
    def user_by_id(self, user_id):
        return next((u for u in self.users.values() if u.user_id == user_id), None)

    @read
    def role(self, user_id, project_id):
        return self.memberships.get((project_id, user_id))

    def _scope(self, ctx, owner=False):
        role = self.role(ctx.user_id, ctx.project_id)
        if role is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        if owner and role != "owner":
            raise AppError(403, "OWNER_REQUIRED", "此操作需要项目负责人权限")

    @read
    def projects(self, user_id):
        if self.user_by_id(user_id) is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        return [
            p.model_copy(update={"role": self.role(user_id, p.project_id)})
            for p in self.project_records.values()
            if self.role(user_id, p.project_id)
        ]

    @write("create_project")
    def create_project(self, user_id, data: ProjectCreate, request_id):
        if self.user_by_id(user_id) is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        with self.lock:
            project = Project(
                project_id=uuid4(),
                name=data.name,
                description=data.description,
                owner_id=user_id,
                role="owner",
            )
            self.project_records[project.project_id] = project
            self.memberships[(project.project_id, user_id)] = "owner"
            return project

    @read
    def members(self, ctx):
        self._scope(ctx, owner=True)
        return [
            Member(user=self.user_by_id(uid), role=role)
            for (pid, uid), role in self.memberships.items()
            if pid == ctx.project_id
        ]

    @write("add_member")
    def add_member(self, ctx, username):
        self._scope(ctx, owner=True)
        user = self.user_by_name(username)
        if user is None:
            raise AppError(404, "USER_NOT_FOUND", "账户不存在")
        with self.lock:
            self.memberships.setdefault((ctx.project_id, user.user_id), "member")
        return Member(user=user, role=self.role(user.user_id, ctx.project_id))

    @write("remove_member")
    def remove_member(self, ctx, user_id):
        self._scope(ctx, owner=True)
        with self.lock:
            if self.role(user_id, ctx.project_id) == "owner":
                raise AppError(409, "OWNER_IMMUTABLE", "不能移除项目负责人")
            self.memberships.pop((ctx.project_id, user_id), None)

    @read
    def papers(self, ctx, year_from=None, year_to=None):
        self._scope(ctx)
        if any(
            v is not None and (not isinstance(v, int) or not 1000 <= v <= 3000) for v in (year_from, year_to)
        ) or (year_from is not None and year_to is not None and year_from > year_to):
            raise invalid()
        return [
            p
            for p in self.paper_records.values()
            if p.project_id == ctx.project_id
            and p.paper_id not in self.removed
            and (year_from is None or (p.year is not None and p.year >= year_from))
            and (year_to is None or (p.year is not None and p.year <= year_to))
        ]

    @read
    def paper(self, ctx, paper_id):
        self._scope(ctx)
        paper = self.paper_records.get(paper_id)
        if paper is None or paper.project_id != ctx.project_id or paper_id in self.removed:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        return paper

    @read
    def sources(self, ctx, paper_id):
        self.paper(ctx, paper_id)
        return deepcopy(self.source_records.get(paper_id, []))

    @write("remove_paper")
    def remove_paper(self, ctx, paper_id):
        self._scope(ctx, owner=True)
        self.paper(ctx, paper_id)
        self.removed.add(paper_id)

    @read
    def evidence(self, ctx, paper_id):
        self.paper(ctx, paper_id)
        return deepcopy(self.evidence_records.get(paper_id, []))

    def _key(self, ctx, operation, key):
        return ctx.user_id, ctx.project_id, operation, key

    def _replay(self, key, fingerprint):
        previous = self.idempotency.get(key)
        if previous and previous[0] != fingerprint:
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同请求")
        return deepcopy(previous[1]) if previous else None

    @write("import_paper")
    def import_paper(self, ctx, title, year, digest, file: FileReference, key):
        self._scope(ctx)
        validate_key(key)
        validate_file(ctx, file)
        if (
            not isinstance(title, str)
            or not 1 <= len(title) <= 200
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or (year is not None and (not isinstance(year, int) or not 1000 <= year <= 3000))
        ):
            raise invalid()
        with self.lock:
            ident = self._key(ctx, "upload", key)
            fingerprint = f"{digest}:{title}:{year}"
            previous = self._replay(ident, fingerprint)
            if previous:
                self.paper(ctx, previous[0].paper_id)
                return previous
            if (ctx.project_id, digest) in self.digests:
                raise AppError(409, "DUPLICATE_PAPER", "当前项目已导入同一文件")
            if file.paper_id in self.paper_records:
                raise AppError(409, "IDENTIFIER_CONFLICT", "资源编号已使用")
            paper = Paper(
                paper_id=file.paper_id, project_id=ctx.project_id, title=title, year=year, status="pending"
            )
            self.paper_records[paper.paper_id] = paper
            task = self.create_task(ctx, "process", {"file": file, "paper_ids": [paper.paper_id]}, key)
            self.digests[(ctx.project_id, digest)] = paper.paper_id
            self.idempotency[ident] = fingerprint, (paper, task)
            return paper, task

    @write("create_task")
    def create_task(self, ctx, kind, payload, key):
        self._scope(ctx)
        validate_key(key)
        payload = normalize_payload(ctx, kind, payload)
        for paper_id in payload["paper_ids"]:
            self.paper(ctx, paper_id)
        with self.lock:
            ident = self._key(ctx, kind, key)
            digest = fingerprint(payload)
            previous = self._replay(ident, digest)
            if previous:
                return previous
            task = Task(
                task_id=uuid4(),
                project_id=ctx.project_id,
                request_id=ctx.request_id,
                kind=kind,
                status=TaskStatus.pending,
                created_at=now(),
                updated_at=now(),
                attempts=0,
            )
            self.jobs[task.task_id] = Job(task, ctx, deepcopy(payload))
            self.idempotency[ident] = digest, task.model_copy(deep=True)
            return task.model_copy(deep=True)

    @read
    def task(self, ctx, task_id):
        self._scope(ctx)
        job = self.jobs.get(task_id)
        if not job or job.task.project_id != ctx.project_id:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        for paper_id in job.payload.get("paper_ids", []):
            self.paper(ctx, paper_id)
        return job.task.model_copy(deep=True)

    @write("retry_task")
    def retry_task(self, ctx, task_id):
        with self.lock:
            self.task(ctx, task_id)
            task = self.jobs[task_id].task
            if task.status != TaskStatus.failed:
                raise AppError(409, "TASK_NOT_RETRYABLE", "仅失败任务可以重试")
            task.status, task.error, task.updated_at = TaskStatus.pending, None, now()
            task.request_id = ctx.request_id
            task.result, task.result_ref = None, None
            self.jobs[task_id].context = ctx
            if task.kind == "process":
                self.paper_records[self.jobs[task_id].payload["file"].paper_id].status = "pending"
            return task.model_copy(deep=True)

    @write("claim_job")
    def claim_job(self):
        with self.lock:
            for job in self.jobs.values():
                if job.task.status == TaskStatus.pending:
                    job.task.status, job.task.updated_at = TaskStatus.processing, now()
                    job.task.attempts += 1
                    if job.task.kind == "process":
                        self.paper_records[job.payload["file"].paper_id].status = "processing"
                    return job
        return None

    def _job_record(self, job):
        stored = self.jobs.get(job.task.task_id)
        if (
            stored is None
            or stored.context.project_id != job.context.project_id
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
            raise AppError(409, "TASK_STATE_CONFLICT", "任务执行次数或状态已变化")
        return stored

    def _validate_sources(self, ctx, sources, allowed_ids=None):
        if len({s.citation_id for s in sources}) != len(sources):
            raise AppError(502, "INVALID_CITATION", "来源编号重复")
        for source in sources:
            self.paper(ctx, source.paper_id)
            if allowed_ids is not None and source.paper_id not in allowed_ids:
                raise AppError(502, "INVALID_CITATION", "来源超出任务范围")
            if not any(
                s.chunk_id == source.chunk_id
                and s.page_number == source.page_number
                and s.excerpt == source.excerpt
                and s.synthetic == source.synthetic
                for s in self.sources(ctx, source.paper_id)
            ):
                raise AppError(502, "INVALID_CITATION", "来源与授权片段不一致")

    @write("complete_job")
    def complete_job(self, job, result):
        with self.lock:
            stored = self._job_record(job)
            self._scope(job.context)
            for pid in job.payload.get("paper_ids", []):
                self.paper(job.context, pid)
            result = normalize_result(job.task.kind, result)
            if stored.task.status == TaskStatus.succeeded and fingerprint(stored.task.result) == fingerprint(
                result
            ):
                return
            if stored.task.status != TaskStatus.processing:
                raise AppError(409, "TASK_STATE_CONFLICT", "任务执行次数或状态已变化")
            if job.task.kind == "process":
                if (
                    result.paper_id != job.payload["file"].paper_id
                    or not result.sources
                    or any(s.paper_id != result.paper_id for s in result.sources)
                    or len({s.chunk_id for s in result.sources}) != len(result.sources)
                    or len({s.citation_id for s in result.sources}) != len(result.sources)
                ):
                    raise AppError(502, "ADAPTER_CONTRACT_ERROR", "论文处理结果不符合契约")
                used = {
                    s.chunk_id
                    for pid, items in self.source_records.items()
                    if pid != result.paper_id
                    for s in items
                }
                if any(s.chunk_id in used for s in result.sources):
                    raise AppError(409, "IDENTIFIER_CONFLICT", "片段编号已使用")
                self.source_records[result.paper_id] = deepcopy(result.sources)
                self.paper_records[result.paper_id].status = "searchable"
            elif job.task.kind == "evidence":
                if result.paper_id != job.payload["paper_ids"][0]:
                    raise AppError(502, "ADAPTER_CONTRACT_ERROR", "证据论文与任务不一致")
                for name in ("research_question", "method", "results", "conclusion", "limitations"):
                    field = getattr(result, name)
                    self._validate_sources(job.context, field.sources, [result.paper_id])
                    if field.status == "pending_verification" and (not field.text or not field.sources):
                        raise AppError(502, "INVALID_CITATION", "证据缺少来源")
                existing = [
                    e
                    for items in self.evidence_records.values()
                    for e in items
                    if e.evidence_id == result.evidence_id
                ]
                if existing:
                    raise AppError(409, "IDENTIFIER_CONFLICT", "证据编号已使用")
                records = self.evidence_records.setdefault(result.paper_id, [])
                records.append(deepcopy(result))
            else:
                self._validate_sources(job.context, result.sources, job.payload["paper_ids"])
                if job.task.kind == "answer" and (
                    len(set(result.citation_ids)) != len(result.citation_ids)
                    or set(result.citation_ids) != {s.citation_id for s in result.sources}
                    or (not result.insufficient_evidence and not result.sources)
                ):
                    raise AppError(502, "INVALID_CITATION", "回答缺少有效引用")
            stored.task.status, stored.task.result, stored.task.updated_at = (
                TaskStatus.succeeded,
                deepcopy(result),
                now(),
            )
            resource_id = (
                result.paper_id
                if job.task.kind == "process"
                else (result.evidence_id if job.task.kind == "evidence" else job.task.task_id)
            )
            stored.task.result_ref = ResultReference(
                resource_type="paper" if job.task.kind == "process" else job.task.kind,
                resource_id=resource_id,
            )

    @write("fail_job")
    def fail_job(self, job, code, message):
        with self.lock:
            stored = self._job_record(job)
            if stored.task.status == TaskStatus.failed and stored.task.error == TaskError(
                code=code, message=message
            ):
                return
            if stored.task.status != TaskStatus.processing:
                raise AppError(409, "TASK_STATE_CONFLICT", "任务执行次数或状态已变化")
            stored.task.status, stored.task.updated_at = TaskStatus.failed, now()
            stored.task.result, stored.task.result_ref = None, None
            stored.task.error = TaskError(code=code, message=message)
            if job.task.kind == "process":
                self.paper_records[job.payload["file"].paper_id].status = "failed"

    @write("save_report")
    def save_report(self, ctx, data: ReportSave, key):
        self._scope(ctx)
        validate_key(key)
        with self.lock:
            ident = self._key(ctx, "report-save", key)
            digest = fingerprint(data)
            previous = self._replay(ident, digest)
            if previous:
                return previous
            self._validate_sources(ctx, data.sources)
            if data.report_id:
                versions = self.versions(ctx, data.report_id)
                if versions[-1].version_id != data.base_version_id:
                    raise AppError(409, "VERSION_CONFLICT", "报告已更新，请读取最新版本后保存")
            else:
                if data.base_version_id:
                    raise AppError(409, "VERSION_CONFLICT", "新报告不能指定基础版本")
                versions = []
            rid = data.report_id or uuid4()
            version = ReportVersion(
                report_id=rid,
                version_id=uuid4(),
                project_id=ctx.project_id,
                number=len(versions) + 1,
                title=data.title,
                research_question=data.research_question,
                body=data.body,
                sources=deepcopy(data.sources),
                created_at=now(),
            )
            self.report_records.setdefault(rid, []).append(version)
            self.idempotency[ident] = digest, version.model_copy(deep=True)
            return version.model_copy(deep=True)

    @read
    def versions(self, ctx, report_id):
        self._scope(ctx)
        versions = self.report_records.get(report_id)
        if not versions or versions[0].project_id != ctx.project_id:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        return deepcopy(versions)

    @read
    def reports(self, ctx):
        self._scope(ctx)
        return [deepcopy(v[-1]) for v in self.report_records.values() if v[0].project_id == ctx.project_id]

    @read
    def controlled_statistics(self, ctx, intent):
        papers = self.papers(ctx)
        if intent == "by_year":
            return ["year", "count"], [
                {"year": y, "count": n} for y, n in Counter(p.year for p in papers).items()
            ]
        if intent == "by_topic":
            return ["topic", "count"], [{"topic": "Mock 合成主题", "count": len(papers)}]
        if intent == "missing_evidence":
            return ["paper_id", "title"], [
                {"paper_id": str(p.paper_id), "title": p.title}
                for p in papers
                if not self.evidence_records.get(p.paper_id)
            ]
        raise AppError(400, "UNSUPPORTED_INTENT", "不支持的统计意图")

    @write("append_audit")
    def append_audit(self, event):
        with self.lock:
            if event.timestamp.tzinfo is None:
                raise invalid()
            existing = next((e for e in self.events if e.event_id == event.event_id), None)
            if existing:
                if existing == event:
                    return
                raise AppError(409, "AUDIT_EVENT_CONFLICT", "审计事件编号已使用")
            if event.project_id is not None and event.project_id not in self.project_records:
                raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
            if event.actor_id is not None and self.user_by_id(event.actor_id) is None:
                raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
            self.events.append(event.model_copy(deep=True))

    @read
    def audit(self, ctx, *, action, decision, actor_id, since, until, offset, limit):
        self._scope(ctx, owner=True)
        if (
            not 0 <= offset
            or not 1 <= limit <= 100
            or (since and since.tzinfo is None)
            or (until and until.tzinfo is None)
            or (since and until and since > until)
        ):
            raise invalid()
        rows = [
            e
            for e in reversed(self.events)
            if e.project_id == ctx.project_id
            and (action is None or e.action == action)
            and (decision is None or e.decision == decision)
            and (actor_id is None or e.actor_id == actor_id)
            and (since is None or e.timestamp >= since)
            and (until is None or e.timestamp <= until)
        ]
        rows.sort(key=lambda e: (e.timestamp, str(e.event_id)), reverse=True)
        return AuditList(items=rows[offset : offset + limit], total=len(rows), offset=offset, limit=limit)
