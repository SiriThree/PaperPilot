"""Project authorization and mandatory audit gates; no SQL executor is enabled by default."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.adapters.ports import Context, DataPort, ProjectRole, SecurityPort
from app.errors import AppError
from app.schemas import AuditDetails, AuditEvent, QueryReason, SecureQueryRequest, SecureQueryResult
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
    policy_id = "postgres-statistics-v2-template"

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
        if candidate_generator is not generate_candidate:
            self.policy_id = (
                "postgres-statistics-v2-model"
                if getattr(candidate_generator, "sql_source", None) == "model"
                else "postgres-statistics-v2-injected"
            )
        self._candidate_generator = candidate_generator
        self._query_executor = query_executor
        self._audit_append = audit_append

    def _audit(
        self, ctx: Context, action: str, decision: str, status: str, start: float, details=None
    ) -> None:
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
                    details=details,
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
        details = AuditDetails(question=question)
        try:
            result = await self._query(ctx, question, start, details)
        except asyncio.CancelledError:
            try:
                self._audit(ctx, "security.query.finished", "undetermined", "cancelled", start, details)
            except AppError:
                pass  # Preserve cancellation even when its audit cannot be confirmed.
            raise
        except AppError as error:
            if error.code != "AUDIT_UNAVAILABLE":
                self._audit(ctx, "security.query.finished", "undetermined", "failed", start, details)
            raise
        self._audit(ctx, "security.query.finished", result.decision, result.execution_status, start, details)
        if result.execution_status == "executed":
            self._project_role(ctx)
        return result

    async def _query(
        self, ctx: Context, question: str, start: float, details: AuditDetails
    ) -> SecureQueryResult:
        try:
            candidate = await self._candidate_generator(ctx, question)
        except AppError as error:
            model_errors = {
                "AI_TIMEOUT": "统计模型请求超时，未执行查询",
                "AI_UNAVAILABLE": "统计模型连接失败、限流或暂不可用，未执行查询",
                "AI_PROVIDER_ERROR": "模型服务拒绝请求，请检查地址、密钥、模型及结构化输出配置",
                "AI_INVALID_RESPONSE": "模型响应不符合候选 SQL 的 JSON 结构，未执行查询",
            }
            if self.policy_id == "postgres-statistics-v2-model" and error.code in model_errors:
                logging.getLogger("paperpilot").warning(
                    "sql_model_failed code=%s request_id=%s", error.code, ctx.request_id
                )
                raise AppError(error.status, error.code, model_errors[error.code]) from None
            raise AppError(503, "GATEWAY_UNAVAILABLE", "安全网关不可用，未执行查询") from None
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
                # Only the trusted, rebuilt parameterized plan reaches audit; never raw candidates.
                details.sql = plan.sql
                decision = "allowed"
                if self._query_executor is not None:
                    self._audit(ctx, "security.query.prepared", "allowed", "pending", start, details)
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
    query_executor: QueryExecutor | None = None,
    candidate_generator: CandidateGenerator | None = None,
) -> SecurityPort:
    import os

    from app.security.executor import PostgresQueryExecutor

    if readers is not None and query_executor is not None:
        raise ValueError("Configure only one statistics executor")
    mode = os.getenv("PAPERPILOT_SQL_GENERATOR", "template")
    generator = candidate_generator if candidate_generator is not None else generate_candidate
    if candidate_generator is not None and mode != "template":
        raise ValueError("Configure SQL generation through only one composition path")
    if mode == "model":
        if os.getenv("PAPERPILOT_ENV") not in {"development", "test"}:
            raise ValueError("Model SQL generation currently requires development/test mode")
        from app.ai.client import ModelClient
        from app.ai.settings import ChatSettings
        from app.ai.sql_generation import ModelSQLGenerator
        from app.security.sql_policy import SQL_BY_QUESTION

        prefix = (
            "PAPERPILOT_SQL_MODEL_"
            if any(k.startswith("PAPERPILOT_SQL_MODEL_") for k in os.environ)
            else "PAPERPILOT_AI_"
        )
        generator = ModelSQLGenerator(ModelClient(ChatSettings.from_env(prefix)), SQL_BY_QUESTION)
    elif mode != "template":
        raise ValueError("PAPERPILOT_SQL_GENERATOR must be template or model")
    return SecurityAdapter(
        data,
        candidate_generator=generator,
        query_executor=PostgresQueryExecutor(readers) if readers is not None else query_executor,
        audit_append=audit_append,
    )
