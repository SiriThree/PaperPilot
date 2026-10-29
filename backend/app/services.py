import asyncio
import logging
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from pydantic import ValidationError

from app.adapters.ports import Adapters
from app.errors import AppError
from app.schemas import (
    AnswerResult,
    AuditEvent,
    DraftResult,
    EvidenceResult,
    ProcessResult,
    QuestionRequest,
    SearchRequest,
    SearchResult,
    SecureQueryResult,
)


class ResearchService:
    def __init__(self, adapters: Adapters, *, job_timeout_seconds=90.0, audit_ready=None, system_audit=None):
        self.adapters = adapters
        if job_timeout_seconds <= 0:
            raise ValueError("Job timeout must be positive")
        self.job_timeout_seconds = job_timeout_seconds
        self.audit_ready = audit_ready
        self.system_audit = system_audit
        self.worker_claim_unavailable = False

    def audit(
        self,
        ctx,
        action,
        resource_type,
        decision="allowed",
        execution_status="completed",
        duration_ms=0,
        policy_id=None,
    ):
        try:
            self.adapters.data.append_audit(
                AuditEvent(
                    event_id=uuid4(),
                    timestamp=datetime.now(UTC),
                    request_id=ctx.request_id,
                    actor_id=ctx.user_id,
                    project_id=ctx.project_id,
                    action=action,
                    resource_type=resource_type,
                    decision=decision,
                    policy_id=policy_id,
                    execution_status=execution_status,
                    duration_ms=duration_ms,
                )
            )
        except Exception:
            raise AppError(503, "AUDIT_UNAVAILABLE", "审计暂不可用，请求未返回执行结果") from None

    def identity_audit(self, user_id, request_id, decision, *, mock=True):
        try:
            (self.system_audit or self.adapters.data.append_audit)(
                AuditEvent(
                    event_id=uuid4(),
                    timestamp=datetime.now(UTC),
                    request_id=request_id,
                    actor_id=user_id,
                    project_id=None,
                    action="auth.login",
                    resource_type="session",
                    decision=decision,
                    # This is the pre-session audit gate: an allowed identity
                    # check does not prove that session creation has committed.
                    execution_status="pending" if decision == "allowed" else "completed",
                    duration_ms=0,
                    mock=mock,
                )
            )
        except Exception:
            raise AppError(503, "AUDIT_UNAVAILABLE", "审计暂不可用，未创建登录会话") from None

    def session_logout_audit(self, user_id, request_id):
        try:
            (self.system_audit or self.adapters.data.append_audit)(
                AuditEvent(
                    event_id=uuid4(),
                    timestamp=datetime.now(UTC),
                    request_id=request_id,
                    actor_id=user_id,
                    project_id=None,
                    action="auth.logout",
                    resource_type="session",
                    decision="allowed",
                    execution_status="pending",
                    duration_ms=0,
                    mock=False,
                )
            )
        except Exception:
            raise AppError(503, "AUDIT_UNAVAILABLE", "审计暂不可用，退出状态未确认") from None

    def secure_query_access_denied(self, request_id, actor_id, *, mock):
        """Record an early rejection without asserting an untrusted project exists."""
        try:
            (self.system_audit or self.adapters.data.append_audit)(
                AuditEvent(
                    event_id=uuid4(),
                    timestamp=datetime.now(UTC),
                    request_id=request_id,
                    actor_id=actor_id,
                    project_id=None,
                    action="secure_query.access_denied",
                    resource_type="secure_query",
                    decision="rejected",
                    execution_status="not_executed",
                    duration_ms=0,
                    mock=mock,
                )
            )
        except Exception:
            raise AppError(503, "AUDIT_UNAVAILABLE", "审计暂不可用，未执行查询") from None

    def validate_sources(self, ctx, sources, allowed=None, *, source_data=None):
        seen = set()
        source_data = self.adapters.data if source_data is None else source_data
        # Only this validation call: never retain data/authorization across requests.
        actual_by_paper = {}
        allowed_keys = (
            None
            if allowed is None
            else {
                (s.citation_id, s.paper_id, s.chunk_id, s.page_number, s.excerpt, s.synthetic)
                for s in allowed
            }
        )
        for source in sources:
            if source.citation_id in seen:
                raise AppError(502, "INVALID_CITATION", "来源编号重复")
            seen.add(source.citation_id)
            if source.paper_id not in actual_by_paper:
                actual_by_paper[source.paper_id] = {
                    (s.chunk_id, s.page_number, s.excerpt, s.synthetic)
                    for s in source_data.sources(ctx, source.paper_id)
                }
            if (source.chunk_id, source.page_number, source.excerpt, source.synthetic) not in actual_by_paper[
                source.paper_id
            ]:
                raise AppError(502, "INVALID_CITATION", "来源与授权片段不一致")
            if (
                allowed_keys is not None
                and (
                    source.citation_id,
                    source.paper_id,
                    source.chunk_id,
                    source.page_number,
                    source.excerpt,
                    source.synthetic,
                )
                not in allowed_keys
            ):
                raise AppError(502, "INVALID_CITATION", "引用不属于本次检索结果")

    async def search(self, ctx, query, *, paper_ids=None):
        try:
            if paper_ids is None:
                raw = await self.adapters.ai.search(ctx, query)
            else:
                scoped = getattr(self.adapters.ai, "search_scoped", None)
                if not callable(scoped):
                    raise AppError(503, "AI_SCOPE_UNAVAILABLE", "检索模块尚不支持指定论文范围")
                raw = await scoped(ctx, query, paper_ids)
            result = SearchResult.model_validate(raw)
            if result.mode != query.mode or len(result.items) > query.limit:
                raise ValueError("invalid search metadata")
            if paper_ids is not None and any(h.paper.paper_id not in paper_ids for h in result.items):
                raise ValueError("invalid search scope")
            self.validate_sources(ctx, [hit.source for hit in result.items])
            papers = {}
            for hit in result.items:
                if hit.paper.paper_id not in papers:
                    papers[hit.paper.paper_id] = self.adapters.data.paper(ctx, hit.paper.paper_id)
                paper = papers[hit.paper.paper_id]
                if (
                    hit.paper != paper
                    or hit.source.paper_id != paper.paper_id
                    or paper.status != "searchable"
                ):
                    raise ValueError("invalid search paper")
                if query.year_from is not None and (paper.year is None or paper.year < query.year_from):
                    raise ValueError("invalid year filter")
                if query.year_to is not None and (paper.year is None or paper.year > query.year_to):
                    raise ValueError("invalid year filter")
            return result
        except (ValidationError, ValueError):
            raise AppError(502, "ADAPTER_CONTRACT_ERROR", "检索模块响应不符合契约") from None

    def question_task(self, ctx, kind, question, key):
        ids = question.paper_ids or [
            p.paper_id for p in self.adapters.data.papers(ctx) if p.status == "searchable"
        ]
        for paper_id in ids:
            self.adapters.data.paper(ctx, paper_id)
        self.audit(ctx, f"{kind}.requested", kind, execution_status="pending")
        try:
            return self.adapters.data.create_task(ctx, kind, {"question": question, "paper_ids": ids}, key)
        except AppError:
            raise
        except Exception:
            raise AppError(503, "DATA_COMMIT_UNKNOWN", "任务提交结果待确认，请使用原幂等键核对") from None

    async def run_job(self, job):
        ctx, task = job.context, job.task
        committed = False
        completing = False
        index = None
        try:
            if self.adapters.data.role(ctx.user_id, ctx.project_id) is None:
                raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
            for pid in job.payload.get("paper_ids", []):
                self.adapters.data.paper(ctx, pid)
            if task.kind == "process":
                indexed = getattr(self.adapters.ai, "process_indexed", None)
                if callable(indexed) and callable(getattr(self.adapters.data, "complete_indexed_job", None)):
                    raw, index = await indexed(ctx, task.task_id, job.payload["file"])
                else:
                    raw = await self.adapters.ai.process(ctx, task.task_id, job.payload["file"])
                result = ProcessResult.model_validate(raw)
                if (
                    result.paper_id != job.payload["file"].paper_id
                    or any(s.paper_id != result.paper_id for s in result.sources)
                    or len({s.chunk_id for s in result.sources}) != len(result.sources)
                ):
                    raise ValueError("invalid processing sources")
                # A must commit chunks and searchable status atomically; Mock only demonstrates this boundary.
            else:
                question = job.payload.get("question", QuestionRequest(question="证据提取"))
                if task.kind == "evidence":
                    pid = job.payload["paper_ids"][0]
                    candidates = sorted(
                        self.adapters.data.sources(ctx, pid),
                        key=lambda source: (source.page_number, source.chunk_id),
                    )
                    sources = []
                    chars = 0
                    for source in candidates:
                        if source.paper_id != pid:
                            raise ValueError("invalid evidence source paper")
                        if len(sources) >= 50 or chars + len(source.excerpt) > 60000:
                            break
                        sources.append(source)
                        chars += len(source.excerpt)
                else:
                    search = await self.search(
                        ctx,
                        SearchRequest(query=question.question, mode="hybrid", limit=50),
                        paper_ids=job.payload["paper_ids"],
                    )
                    sources = [h.source for h in search.items if h.paper.paper_id in job.payload["paper_ids"]]
                sources = [s.model_copy(update={"citation_id": str(i + 1)}) for i, s in enumerate(sources)]
                if task.kind == "answer":
                    result = AnswerResult.model_validate(
                        await self.adapters.ai.answer(ctx, question, sources)
                    )
                    self.validate_sources(ctx, result.sources, allowed=sources)
                    if set(result.citation_ids) != {s.citation_id for s in result.sources} or len(
                        set(result.citation_ids)
                    ) != len(result.citation_ids):
                        raise ValueError("invalid answer citation ids")
                    if not result.insufficient_evidence and not result.sources:
                        raise ValueError("unsupported answer")
                elif task.kind == "draft":
                    result = DraftResult.model_validate(await self.adapters.ai.draft(ctx, question, sources))
                    self.validate_sources(ctx, result.sources, allowed=sources)
                else:
                    pid = job.payload["paper_ids"][0]
                    result = EvidenceResult.model_validate(await self.adapters.ai.extract(ctx, pid, sources))
                    if result.paper_id != pid:
                        raise ValueError("invalid evidence paper")
                    for name in ("research_question", "method", "results", "conclusion", "limitations"):
                        field = getattr(result, name)
                        self.validate_sources(ctx, field.sources, allowed=sources)
                        if field.status == "pending_verification" and (not field.text or not field.sources):
                            raise ValueError("evidence requires support")
                    if not sources:
                        result = result.model_copy(
                            update={
                                name: getattr(result, name).model_copy(
                                    update={"text": None, "status": "insufficient_evidence", "sources": []}
                                )
                                for name in (
                                    "research_question",
                                    "method",
                                    "results",
                                    "conclusion",
                                    "limitations",
                                )
                            }
                        )
            completing = True
            if index is None:
                self.adapters.data.complete_job(job, result)
            else:
                self.adapters.data.complete_indexed_job(job, result, index)
            committed = True
            self.audit(ctx, f"{task.kind}.completed", task.kind)
        except AppError as exc:
            if committed:
                # A's real transactional outbox must retain domain audit even when B's follow-up fails.
                logging.getLogger("paperpilot").warning(
                    "task_post_commit_audit_unavailable task_id=%s request_id=%s",
                    task.task_id,
                    ctx.request_id,
                )
            elif exc.code == "DATA_COMMIT_UNKNOWN":
                # Never turn an unacknowledged success into failure; A owns reconciliation/recovery.
                logging.getLogger("paperpilot").warning(
                    "task_commit_unknown task_id=%s request_id=%s", task.task_id, ctx.request_id
                )
            else:
                self._fail_job(job, exc.code, exc.message)
        except Exception:
            if completing and not committed:
                logging.getLogger("paperpilot").warning(
                    "task_commit_unknown task_id=%s request_id=%s", task.task_id, ctx.request_id
                )
            elif not committed:
                self._fail_job(job, "ADAPTER_CONTRACT_ERROR", "模块处理失败或响应不符合契约")

    def _fail_job(self, job, code, message):
        try:
            self.adapters.data.fail_job(job, code, message)
        except Exception:
            # Stale attempts cannot overwrite newer state; failed storage needs A's recovery mechanism.
            logging.getLogger("paperpilot").warning(
                "task_failure_not_committed task_id=%s request_id=%s",
                job.task.task_id,
                job.context.request_id,
            )

    async def worker(self):
        while True:
            try:
                job = self.adapters.data.claim_job()
            except Exception:
                self.worker_claim_unavailable = True
                logging.getLogger("paperpilot").warning("task_claim_unavailable")
                await asyncio.sleep(1)
                continue
            self.worker_claim_unavailable = False
            if job:
                await asyncio.sleep(0.08)
                try:
                    async with asyncio.timeout(self.job_timeout_seconds):
                        await self._run_leased(job)
                except TimeoutError:
                    # Cancellation cannot prove whether a remote model received the request.
                    self._fail_job(job, "TASK_TIMEOUT", "任务处理超时，请核对后重试")
            else:
                await asyncio.sleep(0.04)

    async def _run_leased(self, job):
        renew = getattr(self.adapters.data, "renew_job", None)
        if not callable(renew):
            await self.run_job(job)
            return

        async def heartbeat():
            while True:
                await asyncio.sleep(self.adapters.data.settings.lease_seconds / 3)
                await asyncio.to_thread(renew, job)

        work = asyncio.create_task(self.run_job(job))
        pulse = asyncio.create_task(heartbeat())
        try:
            done, _ = await asyncio.wait({work, pulse}, return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                await work
            else:
                try:
                    pulse.result()
                except Exception:
                    logging.getLogger("paperpilot").warning("task_lease_lost task_id=%s", job.task.task_id)
        finally:
            work.cancel()
            pulse.cancel()
            await asyncio.gather(work, pulse, return_exceptions=True)

    async def secure_query(self, ctx, question):
        # Privilege/definition drift must stop the gateway before any SQL executes.
        # This is a separate database transaction, not a guarantee against concurrent DDL.
        if self.audit_ready is not None:
            try:
                if await asyncio.to_thread(self.audit_ready, ctx) is False:
                    raise RuntimeError("audit boundary unavailable")
            except Exception:
                raise AppError(503, "AUDIT_UNAVAILABLE", "审计安全配置不可用，未执行查询") from None
        self.audit(ctx, "secure_query.requested", "secure_query", execution_status="pending")
        start = perf_counter()
        try:
            result = SecureQueryResult.model_validate(await self.adapters.security.execute(ctx, question))
            if result.request_id != ctx.request_id:
                raise ValueError("request correlation mismatch")
            from fastapi.responses import JSONResponse

            if (result.reason and result.reason.code == "QUERY_RESULT_TOO_LARGE") or len(
                JSONResponse(content=result.model_dump(mode="json")).body
            ) > 256 * 1024:
                raise AppError(503, "QUERY_RESPONSE_TOO_LARGE", "查询结果超过响应大小限制")
        except AppError:
            self.audit(
                ctx,
                "secure_query.finished",
                "secure_query",
                decision="undetermined",
                execution_status="failed",
                duration_ms=(perf_counter() - start) * 1000,
            )
            raise
        except Exception:
            self.audit(
                ctx,
                "secure_query.finished",
                "secure_query",
                decision="undetermined",
                execution_status="failed",
                duration_ms=(perf_counter() - start) * 1000,
            )
            raise AppError(503, "GATEWAY_UNAVAILABLE", "安全网关不可用，未返回查询结果") from None
        self.audit(
            ctx,
            "secure_query.finished",
            "secure_query",
            decision=result.decision,
            execution_status=result.execution_status,
            policy_id=result.policy_id,
            duration_ms=(perf_counter() - start) * 1000,
        )
        return result
