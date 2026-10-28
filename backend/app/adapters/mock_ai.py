from uuid import uuid4

from app.adapters.ports import DataPort
from app.errors import AppError
from app.schemas import (
    AnswerResult,
    DraftResult,
    EvidenceField,
    EvidenceResult,
    ProcessResult,
    SearchHit,
    SearchResult,
    Source,
)


class MockAI:
    def __init__(self, data: DataPort):
        self.data = data

    async def process(self, ctx, task_id, file):
        if file.project_id != ctx.project_id:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        # No actual PDF parsing. Storage reference is opaque and project scoped.
        return ProcessResult(
            paper_id=file.paper_id,
            sources=[
                Source(
                    citation_id="1",
                    paper_id=file.paper_id,
                    chunk_id=uuid4(),
                    page_number=1,
                    excerpt="【Mock 合成片段】上传已进入模拟处理流程；此内容不是从 PDF 提取的原文。",
                )
            ],
        )

    async def search(self, ctx, query):
        return await self.search_scoped(ctx, query, None)

    async def search_scoped(self, ctx, query, paper_ids):
        items = []
        if paper_ids is not None:
            for paper_id in paper_ids:
                self.data.paper(ctx, paper_id)
        if "无结果" not in query.query and "证据不足" not in query.query:
            for paper in self.data.papers(ctx, query.year_from, query.year_to):
                if paper_ids is not None and paper.paper_id not in paper_ids:
                    continue
                if paper.status != "searchable":
                    continue
                for source in self.data.sources(ctx, paper.paper_id):
                    source.citation_id = str(len(items) + 1)
                    items.append(SearchHit(paper=paper, source=source, score=None))
        # Modes are echoed for UI contract testing, not real ranking algorithms.
        return SearchResult(items=items[: query.limit], mode=query.mode)

    async def answer(self, ctx, question, sources):
        if "模拟失败" in question.question:
            raise AppError(502, "AI_UNAVAILABLE", "模拟 AI 服务失败，可重试")
        if not sources:
            return AnswerResult(
                answer="当前范围内证据不足，无法形成有来源的回答。",
                citation_ids=[],
                sources=[],
                insufficient_evidence=True,
            )
        return AnswerResult(
            answer="【Mock 回答】合成示例演示引用与来源的对应关系 [1]，不代表真实研究结论。",
            citation_ids=[sources[0].citation_id],
            sources=[sources[0]],
            insufficient_evidence=False,
        )

    async def extract(self, ctx, paper_id, sources):
        field = (
            EvidenceField(
                text="【Mock】待人工核验的合成证据", status="pending_verification", sources=sources[:1]
            )
            if sources
            else EvidenceField(text=None, status="insufficient_evidence", sources=[])
        )
        return EvidenceResult(
            evidence_id=uuid4(),
            paper_id=paper_id,
            research_question=field,
            method=field,
            results=field,
            conclusion=field,
            limitations=EvidenceField(text=None, status="not_mentioned", sources=[]),
        )

    async def draft(self, ctx, question, sources):
        body = (
            f"# {question.question}\n\n## 主要方法\nMock 合成内容。\n\n## 研究发现\n仅用于页面与接口演示。\n\n## 局限\n未调用真实模型，不可用于效果评测。\n\n## 来源\n"
            + ("合成片段 [1]" if sources else "证据不足")
        )
        return DraftResult(body=body, sources=sources[:1])
