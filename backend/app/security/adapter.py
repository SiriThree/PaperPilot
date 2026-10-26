"""Task 1 port implementation; query execution stays disabled until the gateway is ready."""

from app.adapters.ports import Context, DataPort, SecurityPort
from app.errors import AppError
from app.schemas import QueryReason, SecureQueryResult


class SecurityAdapter:
    policy_id = "security-bootstrap-deny-v1"

    def __init__(self, data: DataPort):
        self.data = data

    def authorize(self, ctx: Context, action: str, role: str) -> None:
        if role not in {"owner", "member"}:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        if action == "project.read":
            return
        if action not in {"members.manage", "paper.remove", "audit.read"}:
            raise AppError(403, "ACTION_FORBIDDEN", "操作未获授权")
        if role != "owner":
            raise AppError(403, "OWNER_REQUIRED", "此操作需要项目负责人权限")

    async def execute(self, ctx: Context, question: str) -> SecureQueryResult:
        try:
            role = self.data.role(ctx.user_id, ctx.project_id)
        except Exception:
            raise AppError(503, "GATEWAY_UNAVAILABLE", "无法确认项目权限，未执行查询") from None
        self.authorize(ctx, "project.read", role)
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
