"""Project-scoped authorization; query execution stays disabled until the gateway is ready."""

from uuid import UUID

from app.adapters.ports import Context, DataPort, ProjectRole, SecurityPort
from app.errors import AppError
from app.schemas import QueryReason, SecureQueryResult


class SecurityAdapter:
    policy_id = "security-bootstrap-deny-v1"

    def __init__(self, data: DataPort):
        self.data = data

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
        # shortcut: deny every query until AST validation, restricted execution and critical audit are ready.
        return SecureQueryResult(
            decision="rejected",
            execution_status="not_executed",
            reason=QueryReason(
                code="QUERY_EXECUTION_DISABLED", message="安全查询执行链路尚未就绪，未执行查询"
            ),
            policy_id=self.policy_id,
            request_id=ctx.request_id,
            mock=False,
        )


def build_security(data: DataPort) -> SecurityPort:
    return SecurityAdapter(data)
