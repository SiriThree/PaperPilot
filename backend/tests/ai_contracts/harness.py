"""C's provider owns fixtures, file access and configured failure injection."""

from dataclasses import dataclass
from uuid import UUID

from app.adapters.ports import AIPort, Context, DataPort, FileReference
from app.schemas import SearchRequest, Source


@dataclass
class AIHarness:
    ai: AIPort
    failing_ai: AIPort
    data: DataPort
    ctx: Context
    other_ctx: Context
    task_id: UUID
    file: FileReference
    query: SearchRequest
    source: Source
    failure_question: str
    is_mock: bool


def build_mock_harness():
    from app.adapters.mock_ai import MockAI
    from app.adapters.mock_data import MockData, seed_id

    data = MockData()
    ctx = Context(seed_id("alice"), seed_id("project-a"), seed_id("contract-request"))
    other = Context(seed_id("eve"), seed_id("project-b"), seed_id("other-contract-request"))
    ai = MockAI(data)
    return AIHarness(
        ai,
        ai,
        data,
        ctx,
        other,
        seed_id("contract-task"),
        FileReference("contract.pdf", seed_id("paper-a"), ctx.project_id),
        SearchRequest(query="检索增强生成", mode="hybrid"),
        data.sources(ctx, seed_id("paper-a"))[0],
        "模拟失败",
        True,
    )
