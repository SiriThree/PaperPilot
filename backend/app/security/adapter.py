"""Project authorization and mandatory audit gates; no SQL executor is enabled by default."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.adapters.ports import Context, DataPort, ProjectRole, SecurityPort
from app.errors import AppError
from app.schemas import AuditEvent, QueryReason, SecureQueryRequest, SecureQueryResult
from app.security.executor import MAX_RESPONSE_BYTES, QueryResultLimit, QueryUnavailable, ReaderConfig
from app.security.sql_policy import (
    CandidateGenerator,
    QueryPlan,
    SQLPolicyRejected,
    generate_candidate,
    prepare_query,
)

QueryExecutor = Callable[[Context, QueryPlan], Awaitable[tuple[list[str], list[dict]]]]
AuditAppend = Callable[[AuditEvent], None]


class SecurityAdapter:
    policy_id = "postgres-statistics-v2"

    def __init__(
        self,
        data: DataPort,
        *,
        candidate_generator: CandidateGenerator = generate_candidate,
        query_executor: QueryExecutor | None = None,
        audit_append: AuditAppend | None = None,
    ):
        if not callable(candidate_generator):
            raise ValueError("Candidate generator must be callable")
        if query_executor is not None and not callable(query_executor):
            raise ValueError("Query executor must be callable")
        if audit_append is not None and not callable(audit_append):
            raise ValueError("Audit append must be callable")
        self.data = data
        self._candidate_generator = candidate_generator
        self._query_executor = query_executor
        self._audit_append = audit_append

    def _audit(self, ctx: Context, action: str, decision: str, status: str, start: float) -> None:
        try:
            (self._audit_append if self._audit_append is not None else self.data.append_audit)(
                AuditEvent(
                    event_id=uuid4(),
                    timestamp=datetime.now(UTC),
                    request_id=ctx.request_id,
                    actor_id=ctx.user_id,
                    project_id=ctx.project_id,
                    action=action,
                    resource_type="secure_query",
                    decision=decision,
                    policy_id=self.policy_id,
                    execution_status=status,
                    duration_ms=(perf_counter() - start) * 1000,
                    mock=False,
                )
            )
        except Exception:
            # An unconfirmed commit may already exist; never rerun the query to resolve it.
            raise AppError(503, "AUDIT_UNAVAILABLE", "关键安全审计未确认，未返回查询结果") from None

    def _project_role(self, ctx: Context) -> ProjectRole:
        if not isinstance(ctx, Context) or not all(
            isinstance(value, UUID) for value in (ctx.user_id, ctx.project_id, ctx.request_id)
        ):
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        try:
            role = self.data.role(ctx.user_id, ctx.project_id)
        except Exception:
            raise AppError(503, "GATEWAY_UNAVAILABLE", "无法确认项目权限，请求未获放行") from None
        if role not in ("owner", "member"):
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        return role

    def authorize(self, ctx: Context, action: str, role: str) -> None:
        if role not in ("owner", "member"):
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        if role != self._project_role(ctx):
            raise AppError(403, "ACTION_FORBIDDEN", "项目权限已变更，请重新发起请求")
        if action == "project.read":
            return
        if action not in ("members.manage", "paper.remove", "audit.read"):
            raise AppError(403, "ACTION_FORBIDDEN", "操作未获授权")
        if role != "owner":
            raise AppError(403, "OWNER_REQUIRED", "此操作需要项目负责人权限")

    async def execute(self, ctx: Context, question: str) -> SecureQueryResult:
        self._project_role(ctx)
        try:
            question = SecureQueryRequest(question=question).question
        except ValidationError:
            raise AppError(422, "VALIDATION_ERROR", "请求格式或字段值不符合契约") from None
        start = perf_counter()
        try:
            result = await self._query(ctx, question, start)
        except asyncio.CancelledError:
            try:
                self._audit(ctx, "security.query.finished", "undetermined", "cancelled", start)
            except AppError:
                pass  # Preserve cancellation even when its audit cannot be confirmed.
            raise
        except AppError as error:
            if error.code != "AUDIT_UNAVAILABLE":
                self._audit(ctx, "security.query.finished", "undetermined", "failed", start)
            raise
        self._audit(ctx, "security.query.finished", result.decision, result.execution_status, start)
        if result.execution_status == "executed":
            self._project_role(ctx)
        return result

    async def _query(self, ctx: Context, question: str, start: float) -> SecureQueryResult:
        try:
            candidate = await self._candidate_generator(ctx, question)
        except Exception:
            raise AppError(503, "GATEWAY_UNAVAILABLE", "安全网关不可用，未执行查询") from None
        self._project_role(ctx)
        decision = "rejected"
        if candidate is None:
            reason = QueryReason(code="UNSUPPORTED_QUERY", message="该问题不在当前统计允许范围内，未执行查询")
        else:
            try:
                plan = prepare_query(candidate, ctx.project_id)
            except SQLPolicyRejected:
                reason = QueryReason(code="SQL_POLICY_REJECTED", message="候选查询不符合安全策略，未执行查询")
            except Exception:
                raise AppError(503, "GATEWAY_UNAVAILABLE", "安全网关不可用，未执行查询") from None
            else:
                decision = "allowed"
                if self._query_executor is not None:
                    self._audit(ctx, "security.query.prepared", "allowed", "pending", start)
                    self._project_role(ctx)
                    try:
                        columns, rows = await self._query_executor(ctx, plan)
                        result = SecureQueryResult(
                            decision="allowed",
                            execution_status="executed",
                            columns=columns,
                            rows=rows,
                            policy_id=self.policy_id,
                            request_id=ctx.request_id,
                            mock=False,
                        )
                        if len(result.model_dump_json().encode("utf-8")) > MAX_RESPONSE_BYTES:
                            raise QueryResultLimit()
                    except QueryUnavailable:
                        raise AppError(
                            503, "GATEWAY_UNAVAILABLE", "受限查询连接不可用，未返回查询结果"
                        ) from None
                    except Exception as error:
                        code = "QUERY_EXECUTION_FAILED"
                        if isinstance(error, TimeoutError):
                            code = "QUERY_TIMEOUT"
                        elif isinstance(error, QueryResultLimit):
                            code = "QUERY_RESULT_TOO_LARGE"
                        return SecureQueryResult(
                            decision="undetermined",
                            execution_status="failed",
                            reason=QueryReason(code=code, message="受限查询未能完成，未返回查询结果"),
                            policy_id=self.policy_id,
                            request_id=ctx.request_id,
                            mock=False,
                        )
                    self._project_role(ctx)
                    return result
                # Explicit composition must supply project-scoped reader credentials to enable SQL.
                reason = QueryReason(
                    code="QUERY_EXECUTION_DISABLED",
                    message="查询已通过安全校验；当前环境未启用执行，未执行查询",
                )
        return SecureQueryResult(
            decision=decision,
            execution_status="not_executed",
            reason=reason,
            policy_id=self.policy_id,
            request_id=ctx.request_id,
            mock=False,
        )


def build_security(
    data: DataPort,
    *,
    readers: Mapping[UUID, ReaderConfig] | None = None,
    audit_append: AuditAppend | None = None,
) -> SecurityPort:
    from app.security.executor import PostgresQueryExecutor

    return SecurityAdapter(
        data,
        query_executor=PostgresQueryExecutor(readers) if readers is not None else None,
        audit_append=audit_append,
    )
