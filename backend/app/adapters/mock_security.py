from app.adapters.ports import DataPort
from app.errors import AppError
from app.schemas import QueryReason, SecureQueryResult


class MockSecurity:
    """Intent fixtures only. No SQL parser, SQL executor, or real attack protection."""

    def __init__(self, data: DataPort):
        self.data = data

    def authorize(self, ctx, action, role):
        if action in {"members.manage", "paper.remove", "audit.read"} and role != "owner":
            raise AppError(403, "OWNER_REQUIRED", "此操作需要项目负责人权限")

    async def execute(self, ctx, question):
        base = {"request_id": ctx.request_id, "policy_id": "mock-intents-v1", "mock": True}
        if question == "模拟网关故障":
            raise AppError(503, "GATEWAY_UNAVAILABLE", "安全网关不可用，未执行查询")
        if question == "模拟执行失败":
            return SecureQueryResult(
                **base,
                decision="undetermined",
                execution_status="failed",
                reason=QueryReason(code="MOCK_EXECUTION_FAILED", message="模拟执行失败，无真实 SQL 执行"),
            )
        intent = {
            "按年份统计论文数量": "by_year",
            "按研究主题统计论文数量": "by_topic",
            "查询尚未完成证据提取的论文": "missing_evidence",
        }.get(question)
        if intent is None:
            return SecureQueryResult(
                **base,
                decision="rejected",
                execution_status="not_executed",
                reason=QueryReason(code="UNSUPPORTED_QUERY", message="Mock 仅支持三条文档约定的统计演示问题"),
            )
        columns, rows = self.data.controlled_statistics(ctx, intent)
        return SecureQueryResult(
            **base, decision="allowed", execution_status="executed", columns=columns, rows=rows
        )
