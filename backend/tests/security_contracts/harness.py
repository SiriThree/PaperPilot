from dataclasses import dataclass

from app.adapters.ports import Context, DataPort, SecurityPort


@dataclass
class SecurityHarness:
    security: SecurityPort
    failing_security: SecurityPort
    unavailable_security: SecurityPort
    data: DataPort
    ctx: Context
    member_ctx: Context
    normal_question: str
    rejected_question: str
    failure_question: str
    unavailable_question: str
    is_mock: bool


def build_mock_harness():
    from app.adapters.mock_data import MockData, seed_id
    from app.adapters.mock_security import MockSecurity

    data = MockData()
    security = MockSecurity(data)
    ctx = Context(seed_id("alice"), seed_id("project-a"), seed_id("contract-request"))
    member_ctx = Context(seed_id("bob"), ctx.project_id, ctx.request_id)
    return SecurityHarness(
        security,
        security,
        security,
        data,
        ctx,
        member_ctx,
        "按年份统计论文数量",
        "不支持的问题",
        "模拟执行失败",
        "模拟网关故障",
        True,
    )
