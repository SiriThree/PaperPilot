"""Project-scoped authorization; query execution stays disabled until the gateway is ready."""

from uuid import UUID

from pydantic import ValidationError

from app.adapters.ports import Context, DataPort, ProjectRole, SecurityPort
from app.errors import AppError
from app.schemas import QueryReason, SecureQueryRequest, SecureQueryResult
from app.security.sql_policy import CandidateGenerator, SQLPolicyRejected, generate_candidate, prepare_query


class SecurityAdapter:
    policy_id = "postgres-statistics-v2"

    def __init__(self, data: DataPort, *, candidate_generator: CandidateGenerator = generate_candidate):
        if not callable(candidate_generator):
            raise ValueError("Candidate generator must be callable")
        self.data = data
        self._candidate_generator = candidate_generator

    def _project_role(self, ctx: Context) -> ProjectRole:
        if not isinstance(ctx, Context) or not all(
            isinstance(value, UUID) for value in (ctx.user_id, ctx.project_id, ctx.request_id)
        ):
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        try:
            role = self.data.role(ctx.user_id, ctx.project_id)
        except Exception:
            raise AppError(503, "GATEWAY_UNAVAILABLE", "无法确认项目权限，未执行操作") from None
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
                prepare_query(candidate, ctx.project_id)
            except SQLPolicyRejected:
                reason = QueryReason(code="SQL_POLICY_REJECTED", message="候选查询不符合安全策略，未执行查询")
            except Exception:
                raise AppError(503, "GATEWAY_UNAVAILABLE", "安全网关不可用，未执行查询") from None
            else:
                decision = "allowed"
                # shortcut: do not execute until the restricted executor and critical audit are ready.
                reason = QueryReason(
                    code="QUERY_EXECUTION_DISABLED",
                    message="查询已通过策略校验；受限执行器尚未接入，未执行查询",
                )
        return SecureQueryResult(
            decision=decision,
            execution_status="not_executed",
            reason=reason,
            policy_id=self.policy_id,
            request_id=ctx.request_id,
            mock=False,
        )


def build_security(data: DataPort) -> SecurityPort:
    return SecurityAdapter(data)
