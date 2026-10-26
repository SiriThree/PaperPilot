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
    def __init__(self, adapters: Adapters):
        self.adapters = adapters

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

    def identity_audit(self, user_id, request_id, decision):
        try:
            self.adapters.data.append_audit(
                AuditEvent(
                    event_id=uuid4(),
                    timestamp=datetime.now(UTC),
                    request_id=request_id,
                    actor_id=user_id,
                    project_id=None,
                    action="auth.login",
                    resource_type="session",
                    decision=decision,
                    execution_status="completed",
                    duration_ms=0,
                )
            )
        except Exception:
            raise AppError(503, "AUDIT_UNAVAILABLE", "审计暂不可用，未创建登录会话") from None

    def validate_sources(self, ctx, sources, allowed=None):
        seen = set()
        for source in sources:
            if source.citation_id in seen:
                raise AppError(502, "INVALID_CITATION", "来源编号重复")
            seen.add(source.citation_id)
            actual = self.adapters.data.sources(ctx, source.paper_id)
            if not any(
                s.chunk_id == source.chunk_id
                and s.page_number == source.page_number
                and s.excerpt == source.excerpt
                and s.synthetic == source.synthetic
                for s in actual
            ):
                raise AppError(502, "INVALID_CITATION", "来源与授权片段不一致")
            if allowed is not None and not any(s == source for s in allowed):
                raise AppError(502, "INVALID_CITATION", "引用不属于本次检索结果")

    async def search(self, ctx, query):
        try:
            result = SearchResult.model_validate(await self.adapters.ai.search(ctx, query))
            if result.mode != query.mode or len(result.items) > query.limit:
                raise ValueError("invalid search metadata")
            self.validate_sources(ctx, [hit.source for hit in result.items])
            for hit in result.items:
                paper = self.adapters.data.paper(ctx, hit.paper.paper_id)
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
        try:
            if self.adapters.data.role(ctx.user_id, ctx.project_id) is None:
                raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
            for pid in job.payload.get("paper_ids", []):
                self.adapters.data.paper(ctx, pid)
            if task.kind == "process":
                result = ProcessResult.model_validate(
                    await self.adapters.ai.process(ctx, task.task_id, job.payload["file"])
                )
                if (
                    result.paper_id != job.payload["file"].paper_id
                    or any(s.paper_id != result.paper_id for s in result.sources)
                    or len({s.chunk_id for s in result.sources}) != len(result.sources)
                ):
                    raise ValueError("invalid processing sources")
                # A must commit chunks and searchable status atomically; Mock only demonstrates this boundary.
            else:
                question = job.payload.get("question", QuestionRequest(question="证据提取"))
                search = await self.search(
                    ctx, SearchRequest(query=question.question, mode="hybrid", limit=50)
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
            completing = True
            self.adapters.data.complete_job(job, result)
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
                logging.getLogger("paperpilot").warning("task_claim_unavailable")
                await asyncio.sleep(1)
                continue
            if job:
                await asyncio.sleep(0.08)
                await self.run_job(job)
            else:
                await asyncio.sleep(0.04)

    async def secure_query(self, ctx, question):
        self.audit(ctx, "secure_query.requested", "secure_query", execution_status="pending")
        start = perf_counter()
        try:
            result = SecureQueryResult.model_validate(await self.adapters.security.execute(ctx, question))
            if result.request_id != ctx.request_id:
                raise ValueError("request correlation mismatch")
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
